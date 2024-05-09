# COMReF Dataset Generator and Helper tools

In this repository you may find the script with which the images of the
COMReF dataset were created. The [`generate.py`](src/generate.py) script
takes a MusicXML file as input and a target path and it generates a 
measure-level dataset from it. To get the MTN annotations use the code
from the [comref_converter](https://github.com/CVC-DAG/comref-converter) package.

Some additional tools are provided for data analysis
- A [script](src/count_tokens.py) to count the number of tokens of each class.
- A [script](src/clean_data.py) to clean the data for specific notation artifacts.
- A [script](src/assess_difficulty.py) to check for certain difficulty priors in
  MusicXML files.

## Requirements

- Python 3.9+ with
  - Open-CV
  - tqdm
  - [comref_converter](https://github.com/CVC-DAG/comref-converter) (not required for the generation script)
- An installation of Inkscape
- An installation of Verovio. Check the [repository](https://github.com/rism-digital/verovio) for more information on
  how to get it up and running.

If Inkscape or Verovio cannot be found on PATH, an exception will be raised
by the `probe_verovio` or the `probe_inkscape` functions warning you.
