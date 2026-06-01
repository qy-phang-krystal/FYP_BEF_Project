import json


REQUIRED_FIELDS = [
    "taxon",  # Species is fundamental
]

RECOMMENDED_FIELDS = [
    "location",  # Nice to have but not always present
]


class ValidationError(Exception):
    pass


class MappingValidator:
    def __init__(self, mapping_path: str):
        self.mapping_path = mapping_path

    def validate(self):
        with open(self.mapping_path) as f:
            mapping = json.load(f)

        # Check required fields
        for field in REQUIRED_FIELDS:
            field_mapping = mapping.get(field)
            if field_mapping is None:
                raise ValidationError(f"Missing required field: {field}")

            source_column = field_mapping.get("source_column")
            inferred_value = field_mapping.get("inferred_value")
            confidence = field_mapping.get("confidence", 0.0)

            has_source_column = (
                source_column is not None
                and str(source_column).strip().lower() != "null"
                and str(source_column).strip() != ""
            )

            # Accept direct source-column mappings even if the model did not supply a high confidence score
            if not has_source_column and inferred_value is None and confidence < 0.5:
                raise ValidationError(
                    f"Low confidence mapping for required field {field}"
                )

        # Warn about missing recommended fields
        for field in RECOMMENDED_FIELDS:
            if mapping.get(field) is None:
                print(f"Warning: Recommended field '{field}' not found in mapping")

        return True