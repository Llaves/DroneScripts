# The scripts

## ProcessFlightCSV.py
This is a small python script that accepts a .csv flight log produced by Drone Log Book and produces a much condensed csv that includes ground elevations and actual height above ground. The output is downsampled by a factor of ten, with one row per second. 

## flight_processor.html 

This is a web page that performs the same functions as ProcessFlightCSV.py, except in the browser, via drag-and-drop. It also produces a graph of the terrain and flight path, and a graph of altitude above ground level. The page also produces a .csv file containing tabular data about the flight. The Excel macro in FlightGraphing.bas will add plots to your Excel workbook. This page requires an elevation proxy to call the OpenTopoData API which does not support CORS. 

## main.py - OpenTopoData Proxy

OpenTopoData.org does not support CORS requests. This proxy accepts API elevation calls in the OpenTopoData format, calls OpenTopoData, then passes the return values to the original caller in a CORS compliant manner.

You can run your own proxy for free on Koyeb. You will need to fork this repo to your own Github account. Then

    Log in to Koyeb.com.

    Click "Create Service".

    Choose GitHub as the deployment method.

    Select your repository (e.g., yourname/elevation-proxy). 

    In the Build and Deployment settings, Koyeb should auto-detect Python. Ensure these settings are filled:

        Builder: Buildpack (Default)

        Run Command: gunicorn -w 4 -k uvicorn.workers.UvicornWorker main:app

        Port: 8000 (or leave it as the default 8000)

    Choose the Nano (Free) instance.

    Click "Deploy".

## FlightGraphing.bas

This is a MS Excel macro to be used in conjuntion with the .csv file produced by ProcessFlightCSV.py. If you have loaded the output csv file into Excel, this macro will produce the terrain + flight path and the altitude above ground level plots.
