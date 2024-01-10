#!/bin/bash


printf -v mxmlFiles "%s " /home/ptorras/Documents/Datasets/COMREF/Originals/MusicXML/*.mxl \
    /home/ptorras/Documents/Datasets/COMREF/Originals/MuseScoreUsers/*.mxl \
    /home/ptorras/Documents/Datasets/COMREF/Originals/StringQuartets/*.mxl \
    /home/ptorras/Documents/Datasets/COMREF/Originals/Lieder/*.mxl


for x in $mxmlFiles
do
    if ! python3 generate.py "$x" /home/ptorras/Documents/Datasets/COMREF_06;
    then
        echo "$x" >> failed.txt
    fi
done