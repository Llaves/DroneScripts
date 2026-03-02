# The scripts

## ProcessFlightCSV.py
This is a small python script that accepts a .csv flight log produced by Drone Log Book and produces a much condensed csv that includes ground elevations and actual height above ground. The output is downsampled by a factor of ten, with one row per second. 

## main.py - OpenTopoData Proxy

OpenTopoData.org does not support CORS requests. This proxy accepts API elevation calls in the OpenTopoData format, calls OpenTopoData, then passes the return values to the original caller in a CORS compliant manner.

## flight_processor.html 

This is a web page that performs the same functions as ProcessFlightCSV.py, except in the browser, via drag-and-drop. It also produces a graph of the terrain and flight path, and a graph of altitude above ground level.

## FlihthGraphing.bas

This is a MS Excel macro to be used in conjuntion with the .csv file produced by ProcessFlightCSV.py. If you have loaded the output csv file into Excel, this macro will produce the terrain + flight path and the altitude above ground level plots.
