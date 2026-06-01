import json
import random
import time
import os
import pandas as pd
from openai import OpenAI
from util import get_elsevier_api_text, get_wiley_api_text, get_springer_api_text, get_html_safely, fetch_title_abstract, fetch_full_text_xml, smart_extract_text_and_figures, extract_methods_from_html

# OPENAI Client
openai_api_key = "YOUR_OPENAI_API_KEY_HERE"
client = OpenAI(api_key = openai_api_key)

#  PROMPTS
PROMPT_STAGE_1 = """You are an expert academic reviewer screening ecology literature.
Based on the Title and Abstract, evaluate the paper.

CRITERIA:
1. Must be Quantitative Experimental or Observational ecology (e.g., physically measuring biological/environmental variables like species abundance, biomass, yield, or soil metrics). 
2. EXCLUSION LIST: The following paper types are strictly EXCLUDED, even if they mention "data" or "biodiversity":
   - Qualitative social science (e.g., textual analysis, stakeholder interviews, human questionnaires, thematic coding).
   - Policy, legal, or governance documents (e.g., conservation management case studies, legislation reviews).
   - Opinion pieces, perspectives, or literature reviews.
   - Purely theoretical work, simulations, or mathematical modeling that does not use direct physical field/lab measurements.
3. Located in the United Kingdom (England, Scotland, Wales, Britain, Northern Ireland) or elsewhere.

DECISION RULES:
- If the abstract explicitly states quantitative empirical data collection of biological/environmental variables AND explicitly mentions location in the UK, decision is "Include UK".
- If the abstract explicitly states quantitative empirical data collection of biological/environmental variables AND explicitly mentions location outside the UK, decision is "Include non-UK".
- If the abstract falls into ANY category on the EXCLUSION LIST (e.g., purely modeling, policy, qualitative text, or reviews), decision is "Exclude".
- If the methodology (quantitative empirical vs theoretical/qualitative) OR the location is NOT explicitly stated in the abstract, do not guess. The decision MUST be "Unclear".

Respond strictly in valid JSON format:
{
  "data_source": "Brief summary of data source if known, else 'Unknown'",
  "reason": "Brief explanation of decision, explicitly noting if an exclusion category triggered an Exclude",
  "decision": "Include UK", "Include non-UK", "Exclude", or "Unclear"
}
"""

#include not uk
PROMPT_STAGE_2 = """You are an expert academic reviewer. You are now reading the Title, Abstract, AND the Full Methods section of an ecology paper.

CRITERIA:
1. Must be Quantitative Experimental or Observational ecology (e.g., physically measuring biological/environmental variables like species abundance, biomass, yield, or soil metrics in the field or lab).
2. EXCLUSION LIST: The following paper types are strictly EXCLUDED, even if they mention "data" or "biodiversity":
   - Qualitative social science (e.g., textual analysis, stakeholder interviews, human questionnaires, thematic coding).
   - Policy, legal, or governance documents (e.g., conservation management case studies, legislation reviews, EIAs).
   - Opinion pieces, perspectives, or literature reviews.
   - Purely theoretical work, simulations, or mathematical modeling that does not use direct physical field/lab measurements.
3. Located in the United Kingdom (England, Scotland, Wales, Britain, Northern Ireland) or elsewhere.

DECISION RULES:
- If the full methods section explicitly confirms quantitative empirical data collection of biological/environmental variables AND the location is explicitly in the UK, the final decision is "Include UK".
- If the full methods section explicitly confirms quantitative empirical data collection of biological/environmental variables AND the location is explicitly outside the UK, the final decision is "Include non-UK".
- If the methods section falls into ANY category on the EXCLUSION LIST, or lacks clear quantitative empirical biological data, the final decision is "Exclude".

You must make a FINAL decision based on the methods section. Do not guess.

Respond strictly in valid JSON format:
{
  "data_source": "Brief summary of how the quantitative data was acquired (e.g., 'Field survey using quadrats' or 'Soil core sampling')",
  "reason": "Definitive explanation of final decision, explicitly noting if an exclusion category triggered an Exclude",
  "decision": "Include UK", "Include non-UK", or "Exclude"
}
"""

def ask_llm(prompt_template, text_content, model_name="gpt-4o-mini"):
    try:
        response = client.chat.completions.create(
            model = model_name,
            response_format = {"type": "json_object"},
            temperature=0.0,
            messages = [
                {"role": "system", "content" : prompt_template},
                {"role": "user", "content" : text_content}
            ]
        )

        raw_text = response.choices[0].message.content

        # load response as JSON
        output = json.loads(raw_text)

        clean_decision = str(output.get('decision', 'Error')).strip().capitalize()
        return clean_decision, output.get('reason', 'No reason'), output.get('data_source', 'Unknown')
    
    except Exception as e:
        print(f"OpenAI Error: {e}")
        return "System Error", str(e), "Unknown"


def llm_screening(excel = "", num_rows = None):
    data = pd.read_excel(excel)
    data = data.dropna(axis=1, how="all").iloc[:num_rows] 

    decisions, reasonings, data_sources = [], [], []
    # Auto-save file path
    checkpoint_file = "screening_checkpoint_backup_gpt.csv"
    print(f"Note: Progress will auto-save to '{checkpoint_file}' after every paper.\n")


    for index, doi in enumerate(data["DOI"]):
        print(f"\n====================================")
        print(f"Processing paper [{index+1}/{len(data)}]: {doi}")
        
        # --- Abstract Screen ---
        title, abstract = fetch_title_abstract(doi)
        
        if abstract == "Abstract not found":
            print(f"   -> [Warning] Abstract missing. Skipping to Stage 2...")
            decision = "Unclear" 
        else:
            print(f"   -> [Stage 1] Abstract found. Asking LLM...")
            text_stage_1 = f"Title: {title}\n\nAbstract: {abstract}"
            decision, reason, source = ask_llm(PROMPT_STAGE_1, text_stage_1)
            print(f"   -> [Stage 1 Decision]: {decision} ({reason[:60]}...)")

        # --- Full Text/Methods Screen if Decision Unclear ---
        if decision == "Unclear":
            print(f"   -> [Stage 2] Abstract ambiguous. Deploying deep scrape...")
            
            
            xml_content = fetch_full_text_xml(doi)
            if xml_content:
                print(f"   -> [Stage 2a] Open Access XML found - extracting methods...")
                methods_text = smart_extract_text_and_figures(xml_content)
            else:
                print(f"   -> [Stage 2b] Paper paywalled. Checking publisher...")
                
                # Elsevier
                if doi.startswith("10.1016"):
                    methods_text = get_elsevier_api_text(doi)
                    print(methods_text[:500]) # Print the first 500 chars for debugging
            
                # Wiley
                elif doi.startswith("10.1111") or doi.startswith("10.1002"):
                    methods_text = get_wiley_api_text(doi)
                    print(methods_text[:500]) # Print the first 500 chars for debugging

                # Springer
                elif doi.startswith("10.1007") or doi.startswith("10.1038"):
                    methods_text = get_springer_api_text(doi)
                    print(methods_text[:500]) # Print the first 500 chars for debugging

                else:
                    methods_text = "Failed" 
                
                # selenium
                if methods_text == "Failed":
                    print(f"   -> [Fallback] Deploying Selenium...")
                    url = f"https://doi.org/{doi}"
                    html = get_html_safely(url, doi = doi) 
                    methods_text = extract_methods_from_html(html)
            
            print(f"   -> [Stage 2] Text extracted. Asking LLM for final verdict...")
            text_stage_2 = f"Title: {title}\n\nAbstract: {abstract}\n\nExtracted Text: {methods_text}"
            decision, reason, source = ask_llm(PROMPT_STAGE_2, text_stage_2)
            print(f"   -> [Stage 2 Final Decision]: {decision} ({reason[:60]}...)")

        # Save the final outcomes
        decisions.append(decision)
        reasonings.append(reason)
        data_sources.append(source)

        # checkpoint autosave
        temp_data = data.iloc[:len(decisions)].copy()
        temp_data["LLM_decision"] = decisions
        temp_data["LLM_reasons"] = reasonings
        temp_data["LLM_data_source"] = data_sources
        temp_data.to_csv(checkpoint_file, index=False)

        # anti bot 
        wait_time = random.randint(10, 25)
        print(f"   -> [Stealth] Sleeping for {wait_time} seconds to avoid IP bans...")
        time.sleep(wait_time)

    # Save final results
    data["LLM_decision"] = decisions
    data["LLM_reasons"] = reasonings
    data["LLM_data_source"] = data_sources

    final_output = "screening_results_GPT_final.csv"
    data.to_csv(final_output, index=False)
    
    # Clean up the temporary checkpoint file
    if os.path.exists(checkpoint_file):
        os.remove(checkpoint_file)

    print(f"\n===========================================")
    print(f"Screening completed. Results saved to {final_output}")
    print("Summary of Decisions:\n")
    print(data["LLM_decision"].value_counts())

    return data

def filter_bes_links(found_items, context_text="No context provided"):
    """
    The AI Data Bouncer: Evaluates supplementary file descriptions and links
    to download ONLY files containing empirical BES matrix data.
    """
    if not found_items:
        return []

    # Standardize input to a dictionary of {url_or_path: filename_or_description}
    if isinstance(found_items, list):
        items_dict = {str(item): str(item).split('/')[-1] for item in found_items}
    else:
        items_dict = found_items

    prompt = f"""
    You are an expert Ecological Data Curator.
    I am giving you a list of supplementary files extracted from a scientific paper.
    
    PAPER CONTEXT (Abstract, Methods, Main Text Excerpts, & Supplementary Descriptions):
    {context_text}
    
    FILES FOUND:
    {json.dumps(items_dict, indent=2)}
    
    YOUR GOAL:
    Evaluate the file names and the paper context. Select ONLY the files that are highly likely to contain raw or processed empirical tabular data for:
    1. Biodiversity measures (e.g., species abundance, richness, percent cover, density, counts).
    2. Ecosystem functions/services (e.g., biomass, standing stock, respiration, carbon, degradation rates).
    
    NOTE ON CONTEXT: Often, explicit descriptions of supplementary files are missing. If so, read the provided main text/methodology carefully. Infer the contents of the files based on the data collection and statistical analysis described in the main paper.
    
    STRICT INCLUSION/EXCLUSION RULES:
    - KEEP direct tabular data formats: .csv, .tsv, .xlsx, .xls, .txt, or .zip archives. If a file ends in one of these extensions, strongly lean towards including it.
    - KEEP ELSEVIER API LINKS: Links containing "mmc1", "mmc2", "mmc3", etc., are Elsevier's official data files. Even though they lack a .csv extension in the URL, you MUST INCLUDE them if the paper context suggests they contain empirical data.
    - DROP external repository landing pages (e.g., links to Zenodo, Dryad, Figshare, Mendeley, or DOIs starting with 10.5281). These are handled by a different part of our pipeline. You ONLY want direct supplementary files attached to the publisher.
    - DROP code and scripts: .R, .py, .md, .rmd, .sml, .do.
    - DROP purely textual or document formats: .pdf, .docx, .doc (These cannot be safely parsed into a database).
    - DROP genetic sequence data: .fasta, .fastq, .bam, primer tables, phylogenetic trees.
    - DROP irrelevant data: Weather station generic logs, ethical approvals, blank templates.

    CRITICAL RULE FOR JSON OUTPUT: 
    Your output JSON keys MUST be exactly copied from the "FILES FOUND" dictionary provided above. DO NOT invent your own URLs, and DO NOT use the URLs from the example below.

    Respond STRICTLY in valid JSON format.
    
    Example format:
    {{
      "https://publisher.com/real_data_file.csv": {{"decision": "Include", "reasoning": "CSV file containing species abundance matrix."}},
      "https://publisher.com/script.R": {{"decision": "Exclude", "reasoning": "This is a code script, not tabular data."}}
    }}
    """

    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={ "type": "json_object" },
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0 
        )
        
        decisions = json.loads(response.choices[0].message.content)
        approved_items = []
        
        for item_url, details in decisions.items():
            if isinstance(details, dict):
                decision = details.get("decision", "Exclude")
                reason = details.get("reasoning", "No reason provided")
            else:
                decision = str(details)
                reason = "No reason provided"

            filename_preview = items_dict.get(item_url, item_url)[:35]
            if "Include" in decision:
                approved_items.append(item_url)
                print(f"      [+] KEEP : {filename_preview}... -> {reason}")
            else:
                print(f"      [-] DROP : {filename_preview}... -> {reason}")
        
        print(f"   -> [LLM Gatekeeper] Kept {len(approved_items)} out of {len(items_dict)} files.")
        return approved_items

    except Exception as e:
        print(f"   -> [LLM Gatekeeper Error] {e}. Falling back to keeping all potential spreadsheets.")
        # If the LLM crashes, just keep Excel/CSV/TSV/ZIP files via simple text matching
        fallback_approved = [k for k, v in items_dict.items() if str(v).lower().endswith(('.zip', '.xlsx', '.xls', '.csv', '.tsv', '.txt'))]
        return fallback_approved