# An LLM-Driven Pipeline for Mining Biodiversity-Ecosystem Functioning Data

## Overview
This repository contains the Python codebase for a multi-agent AI pipeline designed to automate the extraction and standardisation of macroecological data. The workflow addresses the manual bottleneck in ecological synthesis by using Large Language Models (LLMs) to screen scientific literature, extract supplementary "dark data" via publisher APIs, and securely map highly heterogeneous variables into a canonical Biodiversity-Ecosystem Functioning (BEF) database schema. 

The pipeline strictly prioritises data integrity and hallucination prevention, actively rejecting non-conforming or uncertain ecological data. 

## Repository Structure

The codebase is divided chronologically to reflect the methodology of the automated workflow:

* **`main.py`**: The master orchestrator script used to execute the entire multi-stage pipeline.
* **`screening_and_retrieval/`**: Contains the automated literature screening agent and the data extraction scripts. These scripts evaluate study methodologies and utilise publisher APIs (e.g., Elsevier, Wiley) and browser agents to download valid supplementary tabular data.
* **`database_construction/`**: Contains the agentic workflow scripts (profiling, schema mapping, and validation). These agents align unstandardised raw datasets with the canonical BEF schema while strictly distinguishing between biodiversity and ecosystem function variables.
* **`data/`**: Contains the publicly accessible supplementary datasets downloaded during the retrieval phase, alongside the processed and mapped text files.
* **`outputs/`**: Contains the final compiled database output. *(Note: During the documented run, the final database output was empty, as the validation agents successfully prevented data hallucination by rejecting 100% of the non-conforming extracted datasets).*

## Setup and Installation

**1. Clone the repository:**
```bash
git clone [https://github.com/qy-phang-krystal/FYP_BEF_Project.git](https://github.com/qy-phang-krystal/FYP_BEF_Project.git)
cd FYP_BEF_Project
```

**2. API Keys:**

To run the LLM agents and publisher data retrieval scripts, you must supply your own API keys.

* Create a .env file in the root directory.
* Add your credentials (e.g., OPENAI_API_KEY=your_key_here, alongside any required Elsevier/Wiley API credentials).
* Note: Ensure .env is added to your .gitignore to prevent leaking secrets.

## Usage
To execute the full data extraction and database construction pipeline, run the master orchestrator script from the root directory:
```bash
python main.py
```

The script will sequentially trigger the screening, retrieval, mapping, and validation modules, outputting progress logs to the terminal and saving the resulting files in the data/ and outputs/ directories.

## Note on Downstream Modelling
This repository exclusively hosts the Python architecture for the automated data synthesis pipeline. The downstream R scripts used to calculate the Akaike Information Criterion (AIC) model comparisons, R² strength evaluations, and spatial distribution maps, alongside the curated Moffett et al. (2026) dataset used to drive those models, are available upon request from Will Pearse.
