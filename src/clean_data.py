"""Creates cleaned up version of the dataset.

This script performs data cleaning the following way:
    - Ensures all measures in the notation files are engraved.
    - Ensures all objects lie in a reasonable range within the staff.
    - Ensures all objects lie within temporary bounds of the measure.

It also creates different versions of the data with different delta granularity
"""
import json
import re
from argparse import ArgumentParser, Namespace
from fractions import Fraction
from pathlib import Path
from typing import Dict, List, NamedTuple, Set, Tuple
from xml.etree import ElementTree as ET

from comref_converter import AST, TranslatorXML, VisitorToXML
from comref_converter.visitor_get_tokens import VisitorGetTokens

RE_MEASURE = re.compile(r".+p(.+)_m(.+)\.png")
RE_POSITION = re.compile(r"s:(ANY|[0-9]+)/p:(ANY|\-?[0-9]+)")
RE_DELTA = re.compile(r"DELTA:(\-?[0-9]+/[0-9]+)")


class Identifier(NamedTuple):
    part: str
    measure: str


def _get_img_ids(path: Path) -> Set[Identifier]:
    """Search engraved measure ids within the picture files."""
    output = set()
    for img_path in path.glob("*.png"):
        mch = RE_MEASURE.match(img_path.name)
        if mch is not None:
            output.add(Identifier(part=mch.group(1), measure=mch.group(2)))

    return output


def remove_non_engraved(
    data: Dict[Identifier, AST.Measure], other: Set[Identifier]
) -> Dict[Identifier, AST.Measure]:
    """Remove any measures that have no engraved image associated with them."""
    curr_keys = set(data.keys())
    keys = other.intersection(curr_keys)
    return {k: data[k] for k in keys}


def remove_offside(
    data: Dict[Identifier, AST.Measure], drange: Tuple[int, int]
) -> Tuple[Dict[Identifier, AST.Measure], List[Identifier]]:
    """Remove any measures that contain heavy outliers."""
    output = {}
    outofrange = []
    for k, v in data.items():
        visitor = VisitorGetTokens()
        tokens = visitor.visit_ast(v)

        if all(map(lambda token: _in_range(drange, token), tokens)):
            output[k] = v
        else:
            outofrange.append(k)
    return output, outofrange


def remove_invalid_time(
    data: Dict[Identifier, AST.Measure]
) -> Tuple[Dict[Identifier, AST.Measure], List[Identifier]]:
    """Remove measures with negative time values."""
    output = {}
    outofrange = []
    for k, v in data.items():
        for element in v.elements:
            time = element.delta
            if time is not None and time < 0:
                outofrange.append(k)
                break
        else:
            output[k] = v
    return output, outofrange


def _in_range(drange: Tuple[int, int], token: AST.Token) -> bool:
    return (
        (drange[0] <= token.position.position <= drange[1])
        if token.position.position is not None
        else True
    )


def preprocess_unzipped_mtn(mtn_file: Path) -> ET.Element:
    """Load unzipped xml file."""
    root = ET.parse(mtn_file)
    return root.getroot()


def main(args: Namespace) -> None:
    """Clean the data very clean thank you."""
    removed_overall = {}
    for folder in args.root.glob("*"):
        if not folder.is_dir():
            continue
        print(f"Processing {str(folder)}...")
        tree = preprocess_unzipped_mtn(folder / (folder.name + ".mtn"))
        xml_translator = TranslatorXML()

        score = xml_translator.translate(tree, "", set())

        score_data = {
            Identifier(part=x.part_id, measure=x.measure_id): x for x in score.measures
        }

        if args.tolerance is not None:
            tolerance = (args.tolerance[0], args.tolerance[1])
        else:
            tolerance = (-15, 24)
        data = remove_non_engraved(score_data, _get_img_ids(folder / "measures"))
        data, offside = remove_offside(data, tolerance)
        data, negdelta = remove_invalid_time(data)

        removed_overall[folder.name] = {"offside": offside, "negative_delta": negdelta}

        with open(folder / "removed_on_cleanup.json", "w", encoding="utf-8") as f_out:
            json.dump(removed_overall[folder.name], f_out, indent=4)

        output_data = AST.Score(
            [measure for _, measure in data.items()],
            score_id=score.score_id,
        )
        visitor_xml = VisitorToXML()
        output_xml = visitor_xml.visit_ast(output_data)
        root_element = ET.ElementTree(output_xml)
        ET.indent(root_element, space="    ", level=0)
        root_element.write(folder / (folder.name + "_clean.mtn"))

    removed_output = {
        "offside": [
            f"{fn}_{x}" for fn, v in removed_overall.items() for x in v["offside"]
        ],
        "negative_delta": [
            f"{fn}_{x}"
            for fn, v in removed_overall.items()
            for x in v["negative_delta"]
        ],
    }

    with open(args.root / "removed_on_cleanup.json", "w", encoding="utf-8") as f_out:
        json.dump(removed_output, f_out, indent=4)


def setup() -> Namespace:
    """Parse args and set up stuff."""
    parser = ArgumentParser()

    parser.add_argument(
        "root",
        type=Path,
        help="Root path for the dataset",
    )
    parser.add_argument(
        "--tolerance",
        nargs=2,
        type=int,
        help="Low and high tolerance for in-staff position (both included)",
    )

    args = parser.parse_args()

    return args


if __name__ == "__main__":
    main(setup())
