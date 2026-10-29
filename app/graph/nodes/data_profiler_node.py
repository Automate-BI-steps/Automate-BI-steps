from pathlib import Path
from typing import Dict, List

import pandas as pd


class DataProfiler:

    def __init__(self, input_dir: str = "data/input"):
        self.input_dir = Path(input_dir)

    def load_tables(self) -> Dict[str, pd.DataFrame]:
        tables = {}

        for file in self.input_dir.glob("*.csv"):
            table_name = file.stem

            df = pd.read_csv(
                file,
                encoding="latin1"
            )

            tables[table_name] = df

        return tables

    def profile_table(
        self,
        table_name: str,
        df: pd.DataFrame
    ) -> dict:

        columns = {}

        for column in df.columns:

            series = df[column]

            columns[column] = {
                "dtype": str(series.dtype),
                "row_count": len(series),
                "null_count": int(series.isna().sum()),
                "null_ratio": float(series.isna().mean()),
                "unique_count": int(series.nunique()),
                "unique_ratio": float(
                    series.nunique() / len(series)
                ) if len(series) else 0.0,
            }

        return {
            "table_name": table_name,
            "row_count": len(df),
            "column_count": len(df.columns),
            "columns": columns,
        }

    def profile_all(self) -> Dict[str, dict]:

        tables = self.load_tables()

        return {
            table_name: self.profile_table(
                table_name,
                df
            )
            for table_name, df in tables.items()
        }



def load_database_node(state: dict) -> dict:
    """LangGraph node: load the input CSV tables without changing profiling logic."""
    input_dir = state.get("input_dir", "data/input")
    profiler = DataProfiler(input_dir=input_dir)
    tables_data = profiler.load_tables()
    if not tables_data:
        raise ValueError(f"No CSV files found in {input_dir}/")
    return {
        "tables_data": tables_data,
        "table_names": list(tables_data.keys()),
    }
