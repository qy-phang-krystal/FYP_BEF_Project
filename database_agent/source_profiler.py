from pathlib import Path
import pandas as pd
import json


class SourceProfiler:
    def __init__(self, file_path: str):
        self.file_path = Path(file_path)

    def _get_sample_rows(self, df):
        """Convert DataFrame sample rows to JSON-serializable format"""
        sample_df = df.head(3)
        # Convert to dict and handle datetime serialization
        sample_rows = []
        for _, row in sample_df.iterrows():
            row_dict = {}
            for col, val in row.items():
                if pd.isna(val):
                    row_dict[col] = None
                elif hasattr(val, 'isoformat'):  # datetime-like objects
                    row_dict[col] = val.isoformat()
                else:
                    row_dict[col] = val
            sample_rows.append(row_dict)
        return sample_rows

    def profile(self):
        if self.file_path.suffix == ".csv":
            return self._profile_csv()

        elif self.file_path.suffix == ".tsv":
            return self._profile_tsv()

        elif self.file_path.suffix in [".xlsx", ".xls"]:
            return self._profile_excel()

        elif self.file_path.suffix == ".txt":
            return self._profile_txt()

        else:
            raise ValueError(f"Unsupported file type: {self.file_path}")
    def _is_likely_data_sheet(self, sheet_name: str, df: pd.DataFrame) -> bool:
        sheet_lower = sheet_name.lower()
        metadata_keywords = [
            'metadata', 'instructions', 'notes', 'summary', 'readme',
            'legend', 'key', 'glossary', 'template', 'example',
            'guide', 'help', 'about', 'info', 'reference'
        ]
        if any(kw in sheet_lower for kw in metadata_keywords):
            return False
        if df.shape[0] < 2 or df.shape[1] < 2:
            return False
        return True

    def _clean_header(self, header_row):
        return [str(x).strip() if not pd.isna(x) else "" for x in header_row]

    def _make_unique_header(self, header):
        seen = {}
        unique_header = []
        for col in header:
            if not col:
                unique_header.append(col)
                continue
            count = seen.get(col, 0)
            unique_name = f"{col}_{count+1}" if count else col
            unique_header.append(unique_name)
            seen[col] = count + 1
        return unique_header

    def _score_header_row(self, row):
        clean = self._clean_header(row)
        non_empty = [v for v in clean if v]
        if len(non_empty) < 2:
            return 0
        unique_count = len(set(non_empty))
        string_count = sum(1 for v in non_empty if isinstance(v, str))
        score = unique_count * 2 + string_count - (len(clean) - len(non_empty))
        return score

    def _find_header_row(self, block: pd.DataFrame):
        best_idx = None
        best_score = 0
        for idx, row in block.iterrows():
            score = self._score_header_row(row)
            if score > best_score:
                best_score = score
                best_idx = idx
        return best_idx if best_score >= 4 else None

    def _split_blocks(self, raw: pd.DataFrame):
        empty_mask = raw.isna().all(axis=1)
        blocks = []
        current = []
        for is_empty, (_, row) in zip(empty_mask, raw.iterrows()):
            if is_empty:
                if current:
                    blocks.append(pd.DataFrame(current))
                    current = []
            else:
                current.append(row.values)
        if current:
            blocks.append(pd.DataFrame(current))
        return blocks

    def _parse_sheet_blocks(self, sheet_name: str, raw: pd.DataFrame):
        blocks = self._split_blocks(raw)
        parsed = []
        block_index = 0
        for block in blocks:
            if block.shape[0] < 2 or block.shape[1] < 2:
                continue
            header_idx = self._find_header_row(block)
            if header_idx is None or header_idx >= len(block) - 1:
                continue
            header = self._make_unique_header(self._clean_header(block.iloc[header_idx]))
            data = block.iloc[header_idx + 1 :].copy()
            data.columns = header
            data = data.loc[:, [c for c in header if c]]
            data = data.dropna(how="all", axis=0)
            if data.empty:
                continue
            parsed.append({
                "block_index": block_index,
                "columns": data.columns.tolist(),
                "dtypes": {k: str(v) for k, v in data.dtypes.items()},
                "sample_rows": self._get_sample_rows(data),
                "row_count": len(data),
            })
            block_index += 1
        return parsed

    def _profile_csv(self):
        df = pd.read_csv(self.file_path)

        return {
            "file": self.file_path.name,
            "type": "csv",
            "columns": df.columns.tolist(),
            "dtypes": {k: str(v) for k, v in df.dtypes.items()},
            "sample_rows": self._get_sample_rows(df),
            "row_count": len(df),
        }

    def _profile_tsv(self):
        df = pd.read_csv(self.file_path, sep="\t")

        return {
            "file": self.file_path.name,
            "type": "tsv",
            "columns": df.columns.tolist(),
            "dtypes": {k: str(v) for k, v in df.dtypes.items()},
            "sample_rows": self._get_sample_rows(df),
            "row_count": len(df),
        }

    def _profile_txt(self):
        # Try to detect delimiter for txt files
        with open(self.file_path, "r", encoding="utf-8") as f:
            first_line = f.readline()
        
        # Try common delimiters
        delimiter = None
        for delim in ["\t", ",", "|", " "]:
            if delim in first_line:
                delimiter = delim
                break
        
        if delimiter is None:
            delimiter = "\t"  # Default to tab
        
        df = pd.read_csv(self.file_path, sep=delimiter)

        return {
            "file": self.file_path.name,
            "type": "txt",
            "detected_delimiter": repr(delimiter),
            "columns": df.columns.tolist(),
            "dtypes": {k: str(v) for k, v in df.dtypes.items()},
            "sample_rows": self._get_sample_rows(df),
            "row_count": len(df),
        }

    def _profile_excel(self):
        xls = pd.ExcelFile(self.file_path)

        sheets = {}

        for sheet in xls.sheet_names:
            raw = pd.read_excel(self.file_path, sheet_name=sheet, header=None)
            is_data_sheet = self._is_likely_data_sheet(sheet, raw)
            parsed_tables = []
            if is_data_sheet:
                parsed_tables = self._parse_sheet_blocks(sheet, raw)
                if not parsed_tables:
                    is_data_sheet = False

            sheets[sheet] = {
                "is_data_sheet": is_data_sheet,
                "raw_shape": raw.shape,
                "parsed_tables": parsed_tables,
            }

        return {
            "file": self.file_path.name,
            "type": "excel",
            "sheets": sheets,
        }


if __name__ == "__main__":
    raw_dir = Path("data/raw")

    for file in raw_dir.iterdir():
        profiler = SourceProfiler(file)
        result = profiler.profile()

        output_file = Path("data/processed") / f"{file.stem}_profile.json"

        with open(output_file, "w") as f:
            json.dump(result, f, indent=2)

        print(f"Profiled {file.name}")