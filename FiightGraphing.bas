Attribute VB_Name = "FiightGraphing"
Sub PlotFlights()
    
    Dim ws As Worksheet
    Dim chartObj As ChartObject
    Dim lastRow As Long
    
    ' Set the worksheet (change "Sheet1" to your actual sheet name if needed)
    Set ws = ActiveSheet
    
    ' Find the last row with data in column H (assuming all columns have same length)
    lastRow = ws.Cells(ws.Rows.Count, "H").End(xlUp).Row
    
    ' Delete any existing chart on the worksheet (optional)
    On Error Resume Next
    ws.ChartObjects.Delete
    On Error GoTo 0
    
   
    ' Create a new chart
    Set chartObj = ws.ChartObjects.Add(Left:=500, Width:=750, Top:=50, Height:=500)
    
    ' Add the first series (G as Y, H as X)
    With chartObj.Chart
        ' Set chart type to XY Scatter with smooth lines
        .ChartType = xlXYScatterSmooth
        
        ' Add first series
        .SeriesCollection.NewSeries
        With .SeriesCollection(1)
            .Name = "Ground Elevation"
            .XValues = ws.Range("H2:H" & lastRow)  ' X values from column H
            .Values = ws.Range("G2:G" & lastRow)   ' Y values from column G
        End With
        
        ' Add second series (I as Y, H as X)
        .SeriesCollection.NewSeries
        With .SeriesCollection(2)
            .Name = "Flight Elevation"
            .XValues = ws.Range("H2:H" & lastRow)  ' X values from column H
            .Values = ws.Range("I2:I" & lastRow)   ' Y values from column I
        End With
        
        ' Customize chart appearance (optional)
        .HasTitle = True
        .ChartTitle.Text = "Flight Path over Terrain"
        .Axes(xlCategory).MinimumScale = 0
        
        ' Label axes
        .Axes(xlCategory, xlPrimary).HasTitle = True
        .Axes(xlCategory, xlPrimary).AxisTitle.Text = "Distance Along Flight Path in meters"
        
        .Axes(xlValue, xlPrimary).HasTitle = True
        .Axes(xlValue, xlPrimary).AxisTitle.Text = "Elevation in meters"
        
        ' Add legend
        .HasLegend = True
        .Legend.Position = xlLegendPositionBottom
    End With
    
    ' Optional: Format the lines to make them smooth (though this is already set by chart type)
    With chartObj.Chart.SeriesCollection(1)
        .Smooth = True
        .MarkerStyle = xlMarkerStyleNone  ' Remove markers for cleaner look (optional)
    End With
    
    With chartObj.Chart.SeriesCollection(2)
        .Smooth = True
        .MarkerStyle = xlMarkerStyleNone  ' Remove markers for cleaner look (optional)
    End With
    
        
    With chartObj.Chart.SeriesCollection(2)
        .Smooth = True
        .MarkerStyle = xlMarkerStyleNone  ' Remove markers for cleaner look (optional)
    End With
    
    
    ' create flight elevation profile

    Set chartObj = ws.ChartObjects.Add(Left:=500, Width:=750, Top:=600, Height:=500)
    
    ' Add the first series (J as Y, H as X)
    With chartObj.Chart
        ' Set chart type to XY Scatter with smooth lines
        .ChartType = xlXYScatterSmooth
        
        ' Add first series
        .SeriesCollection.NewSeries
        With .SeriesCollection(1)
            .Name = "Height Above Ground Level"
            .XValues = ws.Range("H2:H" & lastRow)  ' X values from column H
            .Values = ws.Range("J2:J" & lastRow)   ' Y values from column J
        End With
        
       
        ' Customize chart appearance (optional)
        .HasTitle = True
        .ChartTitle.Text = "Height Above Ground Level"
        .Axes(xlCategory).MinimumScale = 0
        .Axes(xlValue).MinimumScale = 0
        
        ' Label axes
        .Axes(xlCategory, xlPrimary).HasTitle = True
        .Axes(xlCategory, xlPrimary).AxisTitle.Text = "Distance Along Flight Path in meters"
        
        .Axes(xlValue, xlPrimary).HasTitle = True
        .Axes(xlValue, xlPrimary).AxisTitle.Text = "Elevation in meters"
        
        ' Add legend
        .HasLegend = True
        .Legend.Position = xlLegendPositionBottom
    End With
    
    ' Optional: Format the lines to make them smooth (though this is already set by chart type)
    With chartObj.Chart.SeriesCollection(1)
        .Smooth = True
        .MarkerStyle = xlMarkerStyleNone  ' Remove markers for cleaner look (optional)
    End With
    


 
    
    
    MsgBox "Dismiss this box to see graphs", vbInformation
    
End Sub
