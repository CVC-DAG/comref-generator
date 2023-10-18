"""The Common Optical Music Recognition Evaluation Framework (COMREF) toolset.

Implementation for the ground truth image generator.

Copyright (C) 2023, Pau Torras <ptorras@cvc.uab.cat>

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""
from __future__ import annotations

import json
import logging
import random
import re
import xml.etree.ElementTree as ET
from argparse import ArgumentParser, Namespace
from pathlib import Path
from shutil import copy, rmtree
from subprocess import run
from typing import Any, Callable, Dict, List, NamedTuple, Tuple
from xml.etree.ElementTree import Element
from zipfile import ZipFile

import cv2
from tqdm.auto import tqdm

RE_FILES = re.compile(r"Output written to .+\/(.+\_[0-9]+\.svg)\.")
RE_NAME = re.compile(r"(.+)\_[0-9]+")
RE_LINE_COMMAND = re.compile(r"M([0-9]+)\s+([0-9]+)\s+L([0-9]+)\s+([0-9]+)")

NAMESPACES = {
    "svg": "http://www.w3.org/2000/svg",
    "mei": "http://www.w3.org/1999/xlink",
}


class BoundingBox(NamedTuple):
    """Represents a bounding box in XYWH format."""

    x: int
    y: int
    w: int
    h: int

    def merge(self, other: BoundingBox) -> BoundingBox:
        """Merge two bounding boxes into one that overlaps both.

        Parameters
        ----------
        other : BoundingBox
            Another bounding box element.

        Returns
        -------
        BoundingBox
            The combined bounding box.
        """
        new_x = min(self.x, other.x)
        new_y = min(self.y, other.y)

        new_w = max(self.x + self.w, other.x + other.w) - new_x
        new_h = max(self.y + self.h, other.y + other.h) - new_y

        return BoundingBox(new_x, new_y, new_w, new_h)


class VerovioError(Exception):
    """An error related to verovio malfunction."""

    ...


class VerovioNotFoundError(Exception):
    """An error related to verovio not being installed."""

    ...


class InkscapeNotFoundError(Exception):
    """An error related to verovio not being installed."""

    ...


class MeasureGenerator:
    """Encapsulates measure generation operations."""

    def __init__(
        self,
        ipath: Path,
        opath: Path,
        hfactor: float,
    ) -> None:
        """Initialise object.

        Parameters
        ----------
        ipath: Path
            Path pointing to the input MXML file.
        opath: Path
            Folder where all output files should be stored.
        hfactor: float
            A factor by which to increase the vertical size of an input measure w.r.t.
            the original staff height. This enlargement is done on both sides of the
            staff.
        """
        self._probe_verovio()
        self._probe_inkscape()

        self._ipath = ipath
        self._opath = opath

        self._hfactor = hfactor

    @staticmethod
    def _open_zip(path: Path) -> Any:
        with ZipFile(path) as f_zip:
            file_list = f_zip.namelist()
            logging.debug("Loading %s from within the MXML file", file_list[-1])
            xml_file = f_zip.open(file_list[-1], "r")
        return ET.parse(xml_file)

    def generate(self) -> None:
        """Perform the main logic of the converter."""
        mxml = self._open_zip(self._ipath)
        print(f"Processing {str(self._ipath)}...")
        output_path = self._opath / self._ipath.stem
        output_path.mkdir(parents=True, exist_ok=True)

        page_path = output_path / "pages"
        page_path.mkdir(parents=False, exist_ok=True)

        measure_path = output_path / "measures"
        measure_path.mkdir(parents=False, exist_ok=True)

        feedback = []
        written = []
        pages: List[str] = []

        print("Copying original mxml into output dir...")
        copy(self._ipath, output_path / self._ipath.name)

        try:
            print("Engraving score using verovio...")
            pages = self._run_verovio(self._ipath, page_path)
        except VerovioError:
            print(f"Could not generate a verovio score from {str(self._ipath)}")
            logging.info(
                "Verovio failed to produce a meaningful output for File "
                "%s. Skipping...",
                str(self._ipath),
            )
            rmtree(output_path)
        except ValueError as exc:
            print(f"Unknown error: {repr(exc)}")
            rmtree(output_path)

        print(f"Done! Produced {len(pages)} pages of music")
        logging.info("Verovio produced %i pages.", len(pages))
        nstaves = self._get_staves(mxml)

        print("Processing page measures...")
        try:
            for page in tqdm(pages, desc="Progress: "):
                curr_fb, current_w = self._process_page_svg(
                    page_path / page, nstaves, measure_path, self._ipath.stem
                )
                feedback += curr_fb
                written += current_w
        except ValueError as exc:
            logging.info("Problem while processing page %s. Skipping...", repr(exc))
            rmtree(output_path)
        with open(output_path / "feedback.json", "w", encoding="utf-8") as f_fb:
            json.dump(feedback, f_fb)
        print("Done!")

    def _process_page_svg(
        self, input_file: Path, staff_info: Dict, output_folder: Path, base_fname: str
    ) -> Tuple[List[Tuple[str, str]], List[str]]:
        """Crop a page input into measure-level images.

        :param input_file: Path to an SVG page.
        :param staff_info: Information to identify a given measure with the part
        it pertains to and its measure number.
        :param output_folder: What folder to write the measure-level images to.
        :returns: A list of measures that lie on the left of the page and a list of
        written files.
        """
        feedback = []
        written = []

        index2part = {y: k for k, x in staff_info.items() for y in x["part_indices"]}

        svg_xml = ET.parse(str(input_file)).getroot()
        png_img = self._svg2img(input_file)

        img_height, img_width, _ = png_img.shape
        canvas_width, canvas_height = self._get_svg_page_size(svg_xml)
        img_size = (img_width, img_height)
        canvas_size = (canvas_width, canvas_height)

        conversor = self._produce_conversor(canvas_size, img_size)

        base_staff_coordinates = self._find_staff_coordinates(svg_xml)
        staff_coordinates = self._merge_staves(base_staff_coordinates, index2part)
        staff_coordinates = self._expand_staves(staff_coordinates, canvas_height)
        staff_coordinates = {k: conversor(v) for k, v in staff_coordinates.items()}

        leftmost_measures = self._find_leftmost(staff_coordinates)

        for k, coord in staff_coordinates.items():
            part_id, measure_id = k
            # system_staff = staff_info[part_id]["part_indices"].index(staff_number) + 1
            crop = png_img[coord.y : coord.y + coord.h, coord.x : coord.x + coord.w, :]
            fname = f"{base_fname}_p{part_id}_m{measure_id}.png"
            cv2.imwrite(str(output_folder / fname), crop)
            written.append(fname)

            if k in leftmost_measures:
                feedback.append(k)

        return feedback, written

    def _merge_staves(
        self,
        staff_coordinates: Dict[Tuple[str, int], BoundingBox],
        index2part: Dict[int, str],
    ) -> Dict[Tuple[str, str], BoundingBox]:
        output: Dict[Tuple[str, str], BoundingBox] = {}

        for ident, bbox in staff_coordinates.items():
            measure_part = (index2part[ident[1]], ident[0])
            if measure_part in output:
                output[measure_part] = output[measure_part].merge(bbox)
            else:
                output[measure_part] = bbox

        return output

    def _expand_staves(
        self,
        coordinates: Dict[Tuple[str, str], BoundingBox],
        page_height: int,
        factor: float = 0.25,
    ) -> Dict[Tuple[str, str], BoundingBox]:
        """Expand measures vertically to an arbitrary size.

        :param coordinates: A dictionary with measure and staff ids as key and
        bounding boxes as values.
        :param factor: Factor of size of the staff to expand by. A factor of 1
        means adding the equivalent of the vertical span of the staff above and
        below the crop.
        :returns: Adjusted bboxes to span the maximum amount of space possible.
        """
        output = {}
        y1values = sorted(
            list(set(bbox.y for bbox in coordinates.values())) + [page_height]
        )
        y2values = sorted(
            [0] + list(set(bbox.y + bbox.h for bbox in coordinates.values()))
        )

        yconv = dict(zip(y1values[:-1], y2values[:-1]))
        hconv = {
            oldy: end - start
            for oldy, start, end in zip(y1values[:-1], y2values[:-1], y1values[1:])
        }

        for key, coord in coordinates.items():
            output[key] = BoundingBox(
                x=coord.x - 720,
                y=yconv[coord.y],
                w=coord.w + (2 * 720),
                h=hconv[coord.y],
            )

        return output

    def _find_leftmost(
        self,
        staff_info: Dict[Tuple[str, str], BoundingBox],
    ) -> List[Tuple[str, str]]:
        """Find measures that lie on the left margin of the page.

        :param staff_info: A Dictionary whose keys are the measure and staff ids in
        MEI terms of a crop and whose values are the bounding boxes of the staves.
        :returns: List of staves on the leftmost area of the page.
        """
        intermediate = {k: bb[0] // 10 for k, bb in staff_info.items()}
        min_value = min(list(intermediate.values()))

        return [k for k, v in intermediate.items() if v == min_value]

    def _find_staff_coordinates(
        self,
        svg_file: Element,
    ) -> Dict[Tuple[str, int], BoundingBox]:
        """Get the placement of measure staves in the score.

        :returns: A dict whose keys are tuples with the measureid and staffid of a
        measure-level staff and values are coordinates in (x,y,w,h) format.
        """
        output = {}
        measures = svg_file.findall(".//svg:g[@class='measure']", NAMESPACES)

        for measure in measures:
            staves = measure.findall(".//svg:g[@class='staff']", NAMESPACES)
            measure_id = str(measure.attrib["data-n"])
            for staff in staves:
                staff_index = int(staff.attrib["data-n"])

                staff_lines_svg = staff.findall("./svg:path", NAMESPACES)
                staff_lines_mch = [
                    RE_LINE_COMMAND.match(line.attrib["d"]) for line in staff_lines_svg
                ]
                assert all(
                    map(lambda x: x is not None, staff_lines_mch)
                ), "Invalid path in staff line definition"

                staff_lines = [
                    tuple(map(int, matches.groups())) for matches in staff_lines_mch
                ]
                xcoords = [y for x in staff_lines for y in x[::2]]
                ycoords = [y for x in staff_lines for y in x[1::2]]
                if len(staff_lines) == 1:
                    bbox = BoundingBox(
                        min(xcoords),
                        min(ycoords) - 90,
                        max(xcoords) - min(xcoords),
                        180,
                    )
                else:
                    bbox = BoundingBox(
                        min(xcoords),
                        min(ycoords),
                        max(xcoords) - min(xcoords),
                        max(max(ycoords) - min(ycoords), 72),
                    )

                output[(measure_id, staff_index)] = bbox

        return output

    def _run_verovio(
        self,
        file_path: Path,
        page_path: Path,
    ) -> List[str]:
        """Run verovio on the specified folder with all-page settings.

        :param file_path: Input file to engrave using Verovio.
        :param page_path: Folder in which to generate the page-level svg's.
        :returns: List of generated files in the folder.
        :raises VerovioError: If verovio fails to generate anything.
        """
        command = self._svg_command(file_path, page_path / f"{file_path.stem}.svg")
        command_output = run(command, capture_output=True, check=False)

        if command_output.returncode != 0:
            raise VerovioError(
                "Verovio failed to produce an output", command_output.stderr
            )

        fnames = command_output.stderr.decode("utf-8").split("\n")
        pages = [
            x.group(1)
            for x in [RE_FILES.match(line) for line in fnames]
            if x is not None
        ]
        return pages

    def _get_staves(
        self,
        xml_tree: Element,
    ) -> Dict[str, Dict[str, Any]]:
        """Assign staves to each Part Identifier.

        :param input_file: Path to the MusicXML input file.
        :returns: A dictionary whose keys are the part id's and whose values are
        other dictionaries. These latter dictionaries contain the number of staves
        of the given part under the "nstaves" key and a list of assigned indices
        under the "staves" key.
        """
        part_list_elm = xml_tree.find("part-list")
        if part_list_elm is None:
            raise ValueError("The MusicXML file has no part-list available.")
        part_list = [
            x.attrib["id"] for ii, x in enumerate(part_list_elm.findall("score-part"))
        ]

        part_elements = xml_tree.findall("part")

        part_staves = {}
        for part in part_elements:
            part_id = part.attrib["id"]
            part_attrib = [
                int(x.text)
                for x in part.findall("measure/attributes/staves")
                if x is not None
            ] or [1]
            curr_staves = max(part_attrib)

            part_staves[part_id] = {"nstaves": curr_staves}

        staff_index = 1
        for ii in part_list:
            nstaves = part_staves[ii]["nstaves"]
            part_staves[ii]["part_indices"] = [
                ii for ii in range(staff_index, staff_index + nstaves)
            ]
            staff_index += nstaves

        return part_staves

    def _get_svg_page_size(
        self,
        svg_xml: Element,
    ) -> Tuple[int, int]:
        """Inspect a Verovio svg xml to find the svg canvas size.

        :param svg_xml: Element tree structure that represents the svg tree in
        question.
        :returns: A tuple with page width and height.
        """
        scale_obj = svg_xml.find('.//svg:svg[@class="definition-scale"]', NAMESPACES)
        if scale_obj is None:
            raise ValueError("No scale object found within output SVG")
        view_box_elm = scale_obj.attrib["viewBox"]
        view_box = tuple(map(int, view_box_elm.split(" ")))[2:]

        return view_box

    def _svg2img(self, input_file: Path) -> ArrayLike:
        """Generate a raster image from an SVG file and load it for cropping.

        Caveats: It produces a file on the pages directory with the image raster
        and loads it back. Requires Inkscape.

        :param input_file: Path to the full page to raster.
        :returns: Rastered image as an array.
        """
        output_file = input_file.parent / (input_file.stem + ".png")
        command = ["inkscape", str(input_file), "-o", str(output_file)]
        _ = run(command, capture_output=True, check=False)
        png_img = cv2.imread(str(output_file), cv2.IMREAD_UNCHANGED)
        png_img[png_img[:, :, 3] == 0] = [255, 255, 255, 255]
        png_img = cv2.cvtColor(png_img, cv2.COLOR_BGRA2BGR)

        return png_img

    @staticmethod
    def _svg_command(
        input_path: Path,
        output_file: Path,
    ) -> List[str]:
        """Build the command to run Verovio with correct intermediate paths.

        :param input_path: Path to the input MusicXML file.
        :param output_file: Path to the intermediate folder where pages should
        be stored. Must contain the dummy page filename at the end (in other
        words, it should not point to the folder, but rather a file within the
        folder).
        :returns: A list of strings with the Verovio command and parameters.
        """
        command = [
            "verovio",
            "--adjust-page-height",
            "--adjust-page-width",
            "-a",
            "--svg-additional-attribute",
            "measure@n",
            "--svg-additional-attribute",
            "staff@n",
            f"{str(input_path)}",
            "-o",
            f"{str(output_file)}",
            "--page-margin-bottom",
            "0",
            "--page-margin-left",
            "0",
            "--page-margin-right",
            "0",
            "--page-margin-top",
            "0",
            "--condense-first-page",
        ]

        return command

    @staticmethod
    def _produce_conversor(
        canvas_size: Tuple[int, int],
        page_size: Tuple[int, int],
    ) -> Callable:
        """Generate a function that scales SVG coordinates to page size.

        Parameters
        ----------
        Tuple[int, int]
            A width, height tuple with the size of the svg canvas.
        Tuple[int, int]
            A width, height tuple with the size of the output image.

        Returns
        -------
        callable
            A function that performs conversion between the input and output image
            coordinate spaces.

        Raises
        ------
        ValueError
            Raised if one of the coordinates is zero.
        """
        if canvas_size[0] == 0 or canvas_size == 0:
            raise ValueError(
                "Input coordinates for either the svg canvas or the output"
                " image are zero."
            )
        width_factor = page_size[0] / canvas_size[0]
        height_factor = page_size[1] / canvas_size[1]

        def conversor(
            bbox: BoundingBox,
            width_factor: float = width_factor,
            height_factor: float = height_factor,
        ) -> BoundingBox:
            x, y, w, h = bbox
            return BoundingBox(
                int(x * width_factor),
                int(y * height_factor),
                int(w * width_factor),
                int(h * height_factor),
            )

        return conversor

    @staticmethod
    def _probe_verovio() -> None:
        """Check whether verovio is installed or not.

        Raises
        ------
        VerovioNotFoundError
            If Verovio is not found when called. It should be installed on the system
            and added to PATH.
        """
        try:
            run(["verovio", "-h base"], capture_output=True, check=False)
        except FileNotFoundError as exc:
            raise exc from VerovioNotFoundError

    @staticmethod
    def _probe_inkscape() -> None:
        """Check whether Inkscape is installed or not.

        Raises
        ------
        InkscapeNotFoundError
            If Inkscape is not found when called. It should be installed on the system
            and added to PATH.
        """
        try:
            run(["inkscape", "--help"], capture_output=True, check=False)
        except FileNotFoundError as exc:
            raise InkscapeNotFoundError(
                "Inkscape is not installed on the system."
            ) from exc


def main(args: Namespace) -> None:
    generator = MeasureGenerator(args.source, args.target, args.hfactor)
    generator.generate()


def setup() -> Namespace:
    parser = ArgumentParser()

    parser.add_argument(
        "source",
        type=Path,
        help="Path to the MXML file to raster.",
    )
    parser.add_argument(
        "target",
        type=Path,
        help="Target directory where all files will be stored",
    )

    parser.add_argument(
        "--hfactor",
        type=float,
        help="Factor by which to scale horizontally.",
        default=0.1,
    )

    args = parser.parse_args()
    return args


if __name__ == "__main__":
    main(setup())
