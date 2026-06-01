from pydantic import BaseModel
from typing import Optional


class CanonicalRecord(BaseModel):
    doi: Optional[str] = None
    study_id: Optional[str] = None
    location: Optional[str] = None
    country: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    taxon: Optional[str] = None
    biodiversity_metric: Optional[str] = None
    biodiversity_value: Optional[float] = None
    ecosystem_function: Optional[str] = None
    ecosystem_function_value: Optional[float] = None
    sample_size: Optional[int] = None
    units: Optional[str] = None
    source_file: Optional[str] = None