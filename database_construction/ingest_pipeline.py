from pathlib import Path
import pandas as pd
from datetime import datetime

from database_agent.source_profiler import SourceProfiler
from database_agent.mapping_agent import MappingAgent
from database_agent.validator import MappingValidator
from database_agent.transformer import Transformer
from database_agent.bes_validator import BESValidator


RAW_DIR = Path("C:\\Users\\Krystal\\OneDrive - Imperial College London\\imperial\\FYP\\FYP_Code\\Data\\raw")
PROCESSED_DIR = Path("C:\\Users\\Krystal\\OneDrive - Imperial College London\\imperial\\FYP\\FYP_Code\\Data\\processed")
MAPPINGS_DIR = Path("C:\\Users\\Krystal\\OneDrive - Imperial College London\\imperial\\FYP\\FYP_Code\\Data\\mappings")
OUTPUT_DIR = Path("C:\\Users\\Krystal\\OneDrive - Imperial College London\\imperial\\FYP\\FYP_Code\\outputs")


class IngestPipeline:
    # BEF column order and mapping from canonical columns
    BEF_COLUMNS = [
        'Initials',
        'Date_data_entered',
        'DOI',
        'Location',
        'Taxon',
        'GPS_latitude_centre',
        'GPS_longitude_centre',
        'spatial_extent_m2',
        'spatial_grain_m2',
        'Experiment_or_Observation',
        'Sample_start_earliest',
        'Sample_end_latest',
        'Sample_date_resolution',
        'Experimental_duration_days',
        'Biome_general',
        'Predominant_landuse',
        'Predominant_landuse_intensity',
        'Manipulation',
        'Biodiversity_value_x',
        'Biodiversiy_value_SD',
        'Biodiversiy_value_N',
        'Biodiversity_x_axis_description',
        'Biodiversity_type',
        'Measurement_unit',
        'Biodiversity_metric',
        'Ecosystem_function_value_y',
        'Ecosystem_function_value_SD',
        'Ecosystem_function_value_N',
        'Ecosystem_function_unit_y_axis',
        'Ecosystem_function_y_axis_description',
        'Ecosystem_function_metric',
        'Data_capture_method',
        'Table_or_figure_number',
    ]

    # Mapping from canonical columns to BEF columns
    CANONICAL_TO_BEF = {
        'doi': 'DOI',
        'location': 'Location',
        'taxon': 'Taxon',
        'latitude': 'GPS_latitude_centre',
        'longitude': 'GPS_longitude_centre',
        'biodiversity_metric': 'Biodiversity_metric',
        'biodiversity_value': 'Biodiversity_value_x',
        'ecosystem_function': 'Ecosystem_function_y_axis_description',
        'ecosystem_function_value': 'Ecosystem_function_value_y',
        'sample_size': 'Biodiversiy_value_N',
        'units': 'Measurement_unit',
        'source_file': 'Data_capture_method',
    }

    def _convert_to_bes_format(self, df: pd.DataFrame) -> pd.DataFrame:
        """Convert canonical dataframe format to BEF database format"""
        bes_df = pd.DataFrame()

        # Map canonical columns to BEF columns
        for canonical_col, bes_col in self.CANONICAL_TO_BEF.items():
            if canonical_col in df.columns:
                bes_df[bes_col] = df[canonical_col]

        # Initialize missing columns with NaN
        for col in self.BEF_COLUMNS:
            if col not in bes_df.columns:
                bes_df[col] = pd.NA

        # Add metadata columns
        initials_value = getattr(self, 'initials', pd.NA)
        bes_df['Initials'] = initials_value if initials_value else pd.NA
        bes_df['Date_data_entered'] = datetime.now().date()

        # Reorder to match BEF column order
        bes_df = bes_df[self.BEF_COLUMNS]

        return bes_df

    def run(self):
        self.initials = input("\nPlease enter your initials for the database (e.g. EM): ").strip()
        transformed_dfs = []

        for source_file in RAW_DIR.iterdir():
            print(f"\nProcessing: {source_file.name}")

            # STEP 1 — PROFILE
            profiler = SourceProfiler(source_file)
            profile = profiler.profile()

            profile_path = PROCESSED_DIR / f"{source_file.stem}_profile.json"

            import json

            with open(profile_path, "w") as f:
                json.dump(profile, f, indent=2)

            print("✓ Profiled")

            # STEP 1.5 — BES VALIDATION
            bes_validator = BESValidator(str(profile_path))
            is_bes_data = bes_validator.validate_bes_content()

            if not is_bes_data:
                print("⚠️  Skipping: Not BES-relevant data")
                continue

            print("✓ BES-validated")

            # STEP 2 — AI MAPPING
            mapper = MappingAgent(str(profile_path))
            mapping = mapper.run()
            mapping_dict = mapping.model_dump() if hasattr(mapping, "model_dump") else mapping
            print(json.dumps(mapping_dict, indent=2))

            mapping_path = MAPPINGS_DIR / f"{source_file.stem}_mapping.json"

            with open(mapping_path, "w") as f:
                json.dump(mapping_dict, f, indent=2)

            print("Mapped")

            # Check for inferred values
            inferred_fields = []
            for field_name, field_mapping in mapping_dict.items():
                if not field_mapping:
                    continue

                inferred_value = field_mapping.get(
                    "inferred_value"
                )

                if inferred_value:
                    inferred_fields.append(
                        f"{field_name}={inferred_value}"
                    )

            if inferred_fields:
                print(
                    "   → Inferred from paper: "
                    + ", ".join(inferred_fields)
                )

            # STEP 3 — VALIDATION
            validator = MappingValidator(str(mapping_path))
            validator.validate()

            print("✓ Validated")
            
            # STEP 4 — TRANSFORMATION
            transformer = Transformer(
                str(source_file),
                str(mapping_path),
            )

            canonical_df = transformer.transform()

            # Check if this source has any actual biodiversity/ecosystem data
            has_data = (
                not canonical_df['biodiversity_value'].isna().all() or
                not canonical_df['ecosystem_function_value'].isna().all()
            )
            if not has_data:
                print(f"⚠️  Skipping {source_file.name}: No actual biodiversity/ecosystem function values")
                continue

            transformed_dfs.append(canonical_df)

            print("✓ Transformed")

        # STEP 5 — MERGE
        if not transformed_dfs:
            print("\nNo valid data files found in this run. Generating empty database template...")
            # Create an empty dataframe with the canonical columns so it can pass through the converter
            master_df = pd.DataFrame(columns=list(self.CANONICAL_TO_BEF.keys()))
        else:
            master_df = pd.concat(transformed_dfs, ignore_index=True)

        # STEP 6 — CLEANING
        if not master_df.empty:
            master_df = master_df.drop_duplicates()

        # STEP 6.5 — CONVERT TO BEF FORMAT
        master_df = self._convert_to_bes_format(master_df)

        # STEP 6.6 — FILTER AND CLEAN
        if not master_df.empty:
            master_df = self._filter_and_clean_bes_data(master_df)

        # STEP 7 — EXPORT
        output_path = OUTPUT_DIR / "master_database.xlsx"

        master_df.to_excel(output_path, index=False)

        print(f"\nDone. Exported to: {output_path}")

    def _filter_and_clean_bes_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """Filter to UK data only, normalize locations, and fill missing DOIs"""
        # Normalize location to UK variants
        df['Location'] = df['Location'].str.strip().str.lower().replace({
            'great britain': 'UK',
            'united kingdom': 'UK',
            'england': 'UK',
            'scotland': 'UK',
            'wales': 'UK',
            'northern ireland': 'UK'
        }, regex=True).str.title()

        # Filter to UK locations only (keep precise UK locations, exclude non-UK)
        uk_keywords = ['uk', 'united kingdom', 'great britain', 'england', 'scotland', 'wales', 'northern ireland']
        df = df[df['Location'].str.lower().isin(uk_keywords) | df['Location'].isna()]

        # For missing locations, set to UK (assuming UK focus)
        df['Location'] = df['Location'].fillna('UK')

        # Fill DOI from filename if missing
        df['DOI'] = df['DOI'].fillna(df['Data_capture_method'].str.extract(r'(10\.\d+_[^_]+(?:\.\d+)*)', expand=False))

        # Don't exclude rows with missing values; keep all UK data
        return df


if __name__ == "__main__":
    pipeline = IngestPipeline()
    pipeline.run()
