# BI Visualization Guidelines & Layout Standards

## Visual Selection Rules
1. **Summary Cards (`card`)**:
   - Best for headline KPIs (Total Imports, Total Exports, Trade Balance, Transaction Count).
   - Must be positioned at the top of the executive overview page for immediate cognitive impact.

2. **Trend & Time-Series (`lineChart`)**:
   - Best for tracking values across time (Date dimension on Category axis).
   - Use multiple lines (Series) or split measures to compare Imports vs. Exports over time.
   - Do not use bar charts for long continuous time horizons.

3. **Categorical Comparison (`clusteredBarChart`)**:
   - Best for ranking categorical entities (Country, Product) by trade value.
   - Limit category display to top performers to avoid cluttered canvases.
   - Breakdown by Direction (Import vs Export) using the Series role provides bilateral insight.

4. **Multi-Attribute Inspection (`tableEx`)**:
   - Best for detailed tabular exploration (Country + Product + Direction + Value).
   - Required for qualitative content such as Business Insights and Data Quality indicators.

5. **Interactive Filtering (`slicer`)**:
   - Recommended for discrete low-cardinality dimensions like Direction ('Import' / 'Export').
   - Position slicers prominently along page headers or left navigation margins.

## Layout Hierarchy & Constraints
- Maximum 6 pages per dashboard report.
- Maximum 6 visuals per page to prevent cognitive overload.
- Ensure all cards have uniform sizes (300x150) and wide charts (580x260).
