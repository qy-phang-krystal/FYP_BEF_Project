import json
import os
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

class BESValidator:
    def __init__(self, profile_path: str):
        self.profile_path = profile_path
        self.client = OpenAI()

    def validate_bes_content(self) -> bool:
        """Check if the data source contains BES-relevant content using LLM analysis"""

        # Load the profile
        with open(self.profile_path) as f:
            profile = json.load(f)

        # Extract key information for analysis
        columns = profile.get("columns", [])
        sample_rows = profile.get("sample_rows", [])
        dtypes = profile.get("dtypes", {})

        if profile.get("type") == "excel":
            columns = []
            sample_rows = []
            dtypes = {}
            for sheet_name, sheet_info in profile.get("sheets", {}).items():
                for table in sheet_info.get("parsed_tables", []):
                    if not columns:
                        columns = table.get("columns", [])
                    dtypes.update(table.get("dtypes", {}))
                    sample_rows.extend(table.get("sample_rows", [])[:3])
                    if len(sample_rows) >= 6:
                        break
                if sample_rows:
                    break

        # Create analysis prompt
        bes_validation_prompt = """
You are an expert ecologist evaluating whether a dataset contains Biodiversity and Ecosystem Services (BES) data suitable for meta-analysis.

BES data typically includes:
- Biodiversity measurements (species richness, abundance, diversity indices)
- Ecosystem function measurements (productivity, decomposition, nutrient cycling)
- Experimental or observational studies linking biodiversity to ecosystem functions
- Spatial information (locations, coordinates)
- Taxonomic information (species names)

EXCLUDE datasets that are:
- Purely taxonomic lists without measurements
- Genetic or molecular data without ecological context
- Modeling results without real measurements
- Non-ecological data (e.g., socioeconomic, purely physiological)

Analyze the column names, data types, and sample values. Determine if this dataset contains measurable biodiversity AND ecosystem function data.

Respond in JSON format with:
- "is_bes_data": boolean (true if contains BES-relevant measurements)
- "confidence": float 0-1 (how confident you are)
- "reason": brief explanation
- "data_types_found": list of BES data types identified (e.g., ["biodiversity_metric", "ecosystem_function"])
"""

        # Prepare content for analysis
        analysis_content = f"""
Dataset Profile:
- Columns: {columns}
- Data Types: {list(dtypes.keys())}
- Sample Data: {sample_rows[:3] if sample_rows else "No sample data"}

Does this contain BES-relevant biodiversity and ecosystem function measurements?
"""

        try:
            response = self.client.chat.completions.create(
                model="gpt-4o-mini",
                temperature=0.0,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": bes_validation_prompt},
                    {"role": "user", "content": analysis_content}
                ]
            )

            result = json.loads(response.choices[0].message.content)

            is_bes = result.get("is_bes_data", False)
            confidence = result.get("confidence", 0.0)
            reason = result.get("reason", "Unknown")
            data_types = result.get("data_types_found", [])

            print(f"   → BES Check: {is_bes} (confidence: {confidence:.2f})")
            print(f"   → Reason: {reason}")
            if data_types:
                print(f"   → BES types found: {', '.join(data_types)}")

            # Only accept if both BES-relevant AND high confidence
            return is_bes and confidence >= 0.7

        except Exception as e:
            print(f"   → BES validation error: {e}")
            # On error, default to including (fail-safe)
            return True