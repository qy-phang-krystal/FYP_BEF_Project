from pathlib import Path
import pandas as pd
import json
import numpy as np


class Transformer:
    # Sheets to skip when processing multi-sheet Excel files
    SKIP_SHEET_KEYWORDS = [
        'metadata', 'instructions', 'notes', 'summary', 'readme',
        'legend', 'key', 'glossary', 'template', 'example',
        'guide', 'help', 'about', 'info', 'reference'
    ]

    def __init__(self, source_file: str, mapping_file: str):
        self.source_file = Path(source_file)
        self.mapping_file = Path(mapping_file)
        self.sheets_used = []
        self.sheets_skipped = []

    def _is_data_sheet(self, sheet_name: str, df: pd.DataFrame) -> bool:
        """Determine if a sheet is likely to contain actual data vs metadata"""
        sheet_lower = sheet_name.lower()
        
        if any(keyword in sheet_lower for keyword in self.SKIP_SHEET_KEYWORDS):
            return False
        if len(df) < 2 or len(df.columns) < 2:
            return False
        return True

    def _clean_header(self, header_row):
        return [str(x).strip() if not pd.isna(x) else "" for x in header_row]

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
        if best_score >= 4:
            return best_idx
        return None

    def _make_unique_header(self, header):
        seen = {}
        unique = []
        for col in header:
            if not col:
                unique.append(col)
                continue
            count = seen.get(col, 0)
            if count:
                unique_col = f"{col}_{count+1}"
            else:
                unique_col = col
            unique.append(unique_col)
            seen[col] = count + 1
        return unique

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
        parsed_dfs = []
        block_index = 0

        for block in blocks:
            if block.shape[0] < 2 or block.shape[1] < 2:
                continue

            header_idx = self._find_header_row(block)
            if header_idx is None or header_idx >= len(block) - 1:
                continue

            header = self._clean_header(block.iloc[header_idx])
            header = self._make_unique_header(header)
            data = block.iloc[header_idx + 1 :].copy()
            data.columns = header
            data = data.loc[:, [c for c in header if c]]
            data = data.dropna(how="all", axis=0)
            if data.empty:
                continue

            data["__sheet_name"] = sheet_name
            data["__sheet_block"] = block_index
            parsed_dfs.append(data)
            block_index += 1

        return parsed_dfs

    def load_dataframe(self):
        if self.source_file.suffix == ".csv":
            return pd.read_csv(self.source_file)

        elif self.source_file.suffix in [".xlsx", ".xls"]:
            xls = pd.ExcelFile(self.source_file)
            dfs = []

            for sheet in xls.sheet_names:
                try:
                    raw = pd.read_excel(self.source_file, sheet_name=sheet, header=None)
                    if not self._is_data_sheet(sheet, raw):
                        self.sheets_skipped.append(sheet)
                        continue

                    sheet_dfs = self._parse_sheet_blocks(sheet, raw)
                    if sheet_dfs:
                        for df in sheet_dfs:
                            dfs.append(df)
                        self.sheets_used.append(sheet)
                    else:
                        self.sheets_skipped.append(sheet)
                except Exception as e:
                    print(f"   → Warning: Could not read sheet '{sheet}': {e}")
                    self.sheets_skipped.append(sheet)

            if dfs:
                return pd.concat(dfs, ignore_index=True)
            else:
                raise ValueError(f"No data sheets found in {self.source_file.name}. Sheets: {xls.sheet_names}")

        else:
            raise ValueError("Unsupported file")

    def transform(self):
        df = self.load_dataframe()
        
        # Log which sheets were used
        if self.sheets_used:
            print(f"   → Data sheets used: {', '.join(self.sheets_used)}")
        if self.sheets_skipped:
            print(f"   → Sheets skipped: {', '.join(self.sheets_skipped)}")

        with open(self.mapping_file) as f:
            mapping = json.load(f)

        canonical = {}

        def normalize_source_column(value):
            if value is None:
                return None
            if isinstance(value, str) and value.strip().lower() == "null":
                return None
            if isinstance(value, str) and value.strip() == "":
                return None
            return value

        for target_field, source_info in mapping.items():
            if source_info is None:
                canonical[target_field] = pd.Series(pd.NA, index=df.index)
                continue

            source_col = normalize_source_column(source_info.get("source_column"))
            inferred_val = source_info.get("inferred_value")

            if source_col is not None and source_col in df.columns:
                canonical[target_field] = df[source_col].reset_index(drop=True)
            elif inferred_val is not None:
                canonical[target_field] = pd.Series(inferred_val, index=df.index)
            else:
                canonical[target_field] = pd.Series(pd.NA, index=df.index)

        canonical_df = pd.DataFrame(canonical)

        canonical_df["source_file"] = self.source_file.name

        return canonical_df