import json
from argparse import ArgumentParser, Namespace
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET

from comref_converter import TranslatorXML
from comref_converter.visitor_get_tokens import VisitorGetTokens


def main(args: Namespace) -> None:
    counter = Counter()
    for folder in args.root.glob("*"):
        tl = TranslatorXML()
        vst = VisitorGetTokens()
        if not folder.is_dir():
            continue
        root = ET.parse(folder / (folder.name + "_clean.mtn")).getroot()
        converted = tl.translate(root, "", set())

        counter.update(map(str, vst.visit_ast(converted)))
    with open(args.root / "token_counts.json", "w") as f_out:
        json.dump(counter, f_out)


def setup() -> Namespace:
    """Parse args and set up stuff."""
    parser = ArgumentParser()

    parser.add_argument(
        "root",
        type=Path,
        help="Root path for the dataset",
    )

    args = parser.parse_args()

    return args


if __name__ == "__main__":
    main(setup())
