import json
from pathlib import Path
from typing import Optional

from pydantic import BaseModel
from dotenv import load_dotenv

from openai import OpenAI
import instructor
import re
import sys
import os

# Add the parent directory to sys.path to import util
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from util import fetch_title_abstract, extract_methods_from_html, get_html_safely, smart_extract_text_and_figures, fetch_full_text_xml


load_dotenv()


class FieldMapping(BaseModel):
    source_column: Optional[str]
    inferred_value: Optional[str] = None
    confidence: float


class MappingResponse(BaseModel):
    doi: Optional[FieldMapping] = None
    study_id: Optional[FieldMapping] = None
    location: Optional[FieldMapping] = None
    country: Optional[FieldMapping] = None
    latitude: Optional[FieldMapping] = None
    longitude: Optional[FieldMapping] = None
    taxon: Optional[FieldMapping] = None
    biodiversity_metric: Optional[FieldMapping] = None
    biodiversity_value: Optional[FieldMapping] = None
    ecosystem_function: Optional[FieldMapping] = None
    ecosystem_function_value: Optional[FieldMapping] = None
    sample_size: Optional[FieldMapping] = None
    units: Optional[FieldMapping] = None


client = instructor.from_openai(OpenAI())

class MappingAgent:
    def __init__(self, profile_path: str):
        self.profile_path = profile_path

    def _extract_doi_from_filename(self, filename: str) -> Optional[str]:
        """Extract DOI from filename like '10.1016_j.cub.2021.07.080_ss1.csv'"""
        # Pattern matches DOI format: numbers.digits_letters.digits.digits
        doi_pattern = r'(\d+\.\d+_[^_]+(?:\.\d+)*)'
        match = re.search(doi_pattern, filename)
        if match:
            doi = match.group(1).replace('_', '/')
            return doi
        return None

    def _get_profile_columns(self, profile: dict) -> list[str]:
        columns = []
        if profile.get("type") in ["csv", "tsv", "txt"]:
            columns.extend(profile.get("columns", []))
        elif profile.get("type") == "excel":
            for sheet_info in profile.get("sheets", {}).values():
                for table in sheet_info.get("parsed_tables", []):
                    columns.extend(table.get("columns", []))
        return [col for col in columns if isinstance(col, str)]

    def _find_taxon_column_from_profile(self, profile: dict) -> Optional[str]:
        taxon_keywords = [
            "species", "taxon", "scientific name", "binomial",
            "genus", "family", "organism", "taxa"
        ]
        for col in self._get_profile_columns(profile):
            lower_col = col.strip().lower()
            if any(keyword in lower_col for keyword in taxon_keywords):
                return col
        return None

    def _get_paper_context(self, doi: Optional[str]) -> str:
        """Fetch paper context (title, abstract, methods) for missing field inference"""
        if not doi:
            return "No DOI available for paper context."

        context_parts = []

        try:
            # Get title and abstract
            title, abstract = fetch_title_abstract(doi)
            if title and title != "Title not found":
                context_parts.append(f"Title: {title}")
            if abstract and abstract != "Abstract not found":
                context_parts.append(f"Abstract: {abstract}")

            # Try to get methods from HTML
            try:
                html = get_html_safely(f"https://doi.org/{doi}", doi)
                if html and html != "Failed":
                    methods = extract_methods_from_html(html)
                    if methods and methods != "Methods not found":
                        context_parts.append(f"Methods: {methods}")
            except Exception as e:
                print(f"Warning: Could not fetch HTML methods: {e}")

            # Try to get XML full text
            try:
                xml_content = fetch_full_text_xml(doi)
                if xml_content:
                    xml_text = smart_extract_text_and_figures(xml_content)
                    if xml_text:
                        context_parts.append(f"Full text excerpts: {xml_text}")
            except Exception as e:
                print(f"Warning: Could not fetch XML text: {e}")

        except Exception as e:
            print(f"Warning: Could not fetch paper context: {e}")
            return "Paper context unavailable."

        if context_parts:
            return "\n\n".join(context_parts)
        else:
            return "Limited paper context available."

    def run(self):
        with open(self.profile_path) as f:
            profile = json.load(f)

        # Extract DOI from filename for paper context
        filename = profile.get("file", "")
        doi = self._extract_doi_from_filename(filename)
        paper_context = self._get_paper_context(doi)

        schema = {
            "doi": "Digital object identifier",
            "study_id": "Study identifier",
            "location": "Sampling location",
            "country": "Country",
            "latitude": "Latitude",
            "longitude": "Longitude",
            "taxon": "Species or taxonomic group",
            "biodiversity_metric": "Biodiversity metric name",
            "biodiversity_value": "Numeric biodiversity value",
            "ecosystem_function": "Function metric",
            "ecosystem_function_value": "Numeric function value",
            "sample_size": "Sample size",
            "units": "Measurement units",
        }


        with open(Path(__file__).parent / "mapping_prompt.txt") as f:
            prompt_template = f.read()

        prompt = prompt_template.format(
            schema=json.dumps(schema, indent=2),
            profile=json.dumps(profile, indent=2),
            paper_context=paper_context,
        )

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            response_model=MappingResponse,
            messages=[
                {
                    "role": "system",
                    "content": "You are an expert scientific data harmonization assistant. For fields like location, taxon, and study details that may not exist as columns in the data source, use the paper context to infer appropriate values. When inferring, set source_column to null and provide the inferred information in inferred_value with an appropriate confidence score.",
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
        )

        mapping_dict = response.model_dump()

        if mapping_dict.get("taxon") is None:
            fallback_taxon = self._find_taxon_column_from_profile(profile)
            if fallback_taxon:
                mapping_dict["taxon"] = {
                    "source_column": fallback_taxon,
                    "inferred_value": None,
                    "confidence": 0.95,
                }
                print(f"   → Fallback taxon mapping using profile column: {fallback_taxon}")

        return mapping_dict
    

if __name__ == "__main__":
    processed_dir = Path("data/processed")

    for file in processed_dir.glob("*_profile.json"):
        agent = MappingAgent(str(file))

        mapping = agent.run()

        output_path = Path("data/mappings") / f"{file.stem}_mapping.json"

        with open(output_path, "w") as f:
            f.write(mapping.model_dump_json(indent=2))

        print(f"Mapped {file.name}")
