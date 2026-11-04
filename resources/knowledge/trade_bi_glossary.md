# Trade BI Glossary & Business Definitions

## Overview
This document defines standard business metrics and interpretation conventions for international trade data analysis.

## Key Performance Indicators (KPIs)
- **Total Imports**: Sum of trade value for all transactions where flow direction is 'Import'. Represents the gross inflow of goods into the reporting economy.
- **Total Exports**: Sum of trade value for all transactions where flow direction is 'Export'. Represents gross sales of domestic or national goods abroad.
- **Trade Balance**: Defined as Total Exports minus Total Imports (`[Total Exports] - [Total Imports]`). A positive trade balance indicates a trade surplus, while a negative balance indicates a trade deficit.
- **Transaction Count**: Total count of recorded customs transactions (`COUNTROWS(FactTrade)`). Measures operational volume, activity density, and audit sample size.

## Grain and Dimensions
- **Date**: The transaction or clearance date. Trends should be aggregated monthly, quarterly, or yearly.
- **Country**: The partner country code (ISO-2) representing trading counterparts.
- **Product**: Product category classification or HS sector.
- **Direction**: Trade flow type, strictly restricted to 'Import' or 'Export'.
