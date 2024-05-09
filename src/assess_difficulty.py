import json
from argparse import ArgumentParser, Namespace
from enum import Enum, auto
from pathlib import Path
from typing import Any, Dict
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pandas as pd


def main(args: Namespace) -> None:
    output = {}
    for folder in [x for x in args.root_path.glob("*") if x.is_dir()]:
        mxml_path = [x for x in folder.glob("*.mxl")][0]
        with ZipFile(mxml_path) as f_zip:
            file_list = f_zip.namelist()
            with f_zip.open(file_list[-1], "r") as xml_file:
                root = ET.parse(xml_file)
        output[mxml_path.stem] = {}
        for part in root.findall("part"):
            ident = part.get("id")
            results = analyse_part(part)
            output[mxml_path.stem][ident] = results

    with open(args.root_path / "part_difficulty.json", "w") as f_json:
        json.dump(output, f_json)

    dataframe = pd.DataFrame.from_dict(output)
    dataframe.to_csv(args.root_path / "part_difficulty.csv")


def analyse_part(part: ET.Element) -> Dict[str, Any]:
    output = {
        "max_beaming": 0,
        "max_staves": 1,
        "polyphony_type": "monophonic",
        "staff_type": "single",
    }
    for measure in part:
        measure_properties = analyse_measure(measure)
        if measure_properties["homophony"]:
            output["polyphony_type"] = "homophonic"
        if measure_properties["polyphony"]:
            output["polyphony_type"] = "polyphonic"

        if measure_properties["nstaves"] > 1:
            output["staff_type"] = "multiple"

        output["max_beaming"] = max(
            output["max_beaming"], measure_properties["max_beaming"]
        )
        output["max_staves"] = max(output["max_staves"], measure_properties["nstaves"])

    return output


def analyse_measure(measure: ET.Element) -> Dict[str, Any]:
    output = {
        "homophony": False,
        "polyphony": False,
        "nstaves": 0,
        "max_beaming": 0,
    }
    voices = set()
    for elm in measure:
        if elm.tag == "note":
            print_object = elm.get("print-object")
            if print_object is not None and print_object == "no":
                continue
            output["max_beaming"] = max(output["max_beaming"], len(elm.findall("beam")))
            if elm.find("chord") is not None:
                output["homophony"] = True
            voice = elm.find("voice")
            if voice is not None:
                voices.add(voice.text)
        # elif elm.tag == "backup":
        #     output["polyphony"] = True
        elif elm.tag == "attributes":
            nstaves = elm.find("staves")
            if nstaves is not None:
                output["nstaves"] = max(output["nstaves"], int(nstaves.text))
    if len(voices) > 1:
        output["polyphony"] = True

    return output


def setup() -> Namespace:
    parser = ArgumentParser()
    parser.add_argument("root_path", help="Root COMREF path", type=Path)
    args = parser.parse_args()
    return args


if __name__ == "__main__":
    main(setup())
