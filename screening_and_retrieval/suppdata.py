import re
import requests
from journal_final import (
    suppdata_nature, suppdata_plos, suppdata_wiley, suppdata_elsevier, 
    suppdata_figshare, suppdata_science, suppdata_pnas, suppdata_proceedings, 
    suppdata_epmc, suppdata_biorxiv, suppdata_dryad, suppdata_peerj, 
    suppdata_copernicus, suppdata_mdpi, suppdata_jstatsoft, suppdata_zenodo
    )
from util import get_publisher_from_crossref, scan_for_external_repos

elsevier_api = "YOUR_ELSEVIER_API_KEY_HERE"
wiley_api = "YOUR_WILEY_API_KEY_HERE"

# ==========================================
# 1. PARAMETER EXPANDER 
# ==========================================
def _fix_param(x_list, param, param_name):
    if isinstance(param, list):
        if len(param) != len(x_list):
            if len(x_list) % len(param) != 0:
                raise ValueError(f"Length of {param_name} ({len(param)}) is incompatible with 'dois' ({len(x_list)})")
            repeats = len(x_list) // len(param)
            param = param * repeats
        return param
    return [param] * len(x_list)

# ==========================================
# 2. FUNCTION ROUTER 
# ==========================================
def _get_scraper_function(pub_code):
    func_map = {
        "78": suppdata_elsevier, 
        "297": suppdata_nature,
        "341": suppdata_pnas,
        "340": suppdata_plos,
        "311": suppdata_wiley,
        "221": suppdata_science,
        "175": suppdata_proceedings,
        "246": suppdata_biorxiv,
        "4443": suppdata_peerj,
        "3145": suppdata_copernicus,
        "1968": suppdata_mdpi,
        "7893": suppdata_jstatsoft,
        
        "elsevier": suppdata_elsevier,
        "plos": suppdata_plos,
        "wiley": suppdata_wiley,
        "science": suppdata_science,
        "proceedings": suppdata_proceedings,
        "figshare": suppdata_figshare,
        "biorxiv": suppdata_biorxiv,
        "epmc": suppdata_epmc,
        "dryad": suppdata_dryad,
        "peerj": suppdata_peerj,
        "copernicus": suppdata_copernicus,
        "mdpi": suppdata_mdpi,
        "jstatsoft": suppdata_jstatsoft
    }
    return func_map.get(str(pub_code).lower(), suppdata_epmc)

# ==========================================
# 3. THE MASTER ENTRY POINT 
# ==========================================
def suppdata(dois=None, sis="all", from_pub="auto", save_names=None, save_dir=None, vols=None, issues=None):
    
    if not dois: raise ValueError("'dois' must contain some data!")
    if isinstance(dois, str): dois = [dois]

    sis = _fix_param(dois, sis, "sis")
    from_pubs = _fix_param(dois, from_pub, "from_pub")
    save_names = _fix_param(dois, save_names, "save_names")
    vols = _fix_param(dois, vols, "vols")
    issues = _fix_param(dois, issues, "issues")

    results = {}

    for i in range(len(dois)):
        doi = dois[i]
        si = sis[i]
        pub = from_pubs[i]
        vol = vols[i]
        issue = issues[i]
        
        print(f"\n=======================================================")
        print(f"--- Processing [{i+1}/{len(dois)}]: {doi} ---")
        print(f"=======================================================")
        
        pub_path = "Failed"
        repo_paths = [] # <--- UPGRADE: Now a list to hold multiple repository files!

        if pub == "auto":
            pub = get_publisher_from_crossref(doi)

        # ==========================================
        # TRACK 1: THE PUBLISHER DATA
        # ==========================================
        print(f"\n>>> TRACK 1: Hunting Publisher Supplements...")
        print(f"   -> [Europe PMC] Checking EPMC API...")
        epmc_result = suppdata_epmc(doi, si_index=si, save_dir=save_dir)
            
        if epmc_result not in ["No_Data", "Failed", None]:
            print(f"    -> [Success] Downloaded via EPMC!")
            pub_path = epmc_result
        else:
            print(f"    -> [EPMC Failed] Falling back to direct Publisher Scraper...")
            scraper_func = _get_scraper_function(pub)

            if scraper_func:
                try:
                    if scraper_func == suppdata_proceedings:
                        pub_path = scraper_func(doi, si_index=si, vol=vol, issue=issue, save_dir=save_dir)
                    else:
                        pub_path = scraper_func(doi, si_index=si, save_dir=save_dir)
                except Exception as e:
                    print(f"    -> [SuppData Error] Publisher scrape failed: {e}")
                    pub_path = "Failed"
            else:
                print(f"    -> [Result] No custom scraper for publisher: {pub}")
                pub_path = "Failed"

        # ==========================================
        # TRACK 2: THE REPOSITORY DATA
        # ==========================================
        print(f"\n>>> TRACK 2: Hunting Repository Datasets...")
        external_links = []
        pub_str = str(pub).lower()
        api_failed = False
        
        if pub_str in ["78", "elsevier"]:
            print("   -> [API Scout] Bypassing Selenium. Scanning Elsevier XML for data links...")
            try:
                resp = requests.get(f"https://api.elsevier.com/content/article/doi/{doi}", 
                                    headers={"X-ELS-APIKey": elsevier_api, "Accept": "text/xml"}, timeout=20)
                if resp.status_code == 200:
                    raw_text = resp.content.decode('utf-8', errors='ignore')
                    
                    # 1. Look for standard URL links
                    external_links = re.findall(r'https?://(?:www\.)?(?:figshare\.com|datadryad\.org)[^\s"\'<>\\]+', raw_text)
                    
                    # 2. THE FIX: The Dryad DOI Sniper!
                    # Catch raw DOIs in the XML even if they aren't formatted as URLs
                    dryad_dois = re.findall(r'(10\.5061/dryad\.[a-zA-Z0-9_-]+)', raw_text)
                    for d in dryad_dois:
                        external_links.append(f"https://datadryad.org/stash/dataset/doi:{d}")
                        
            except Exception as e: 
                print(f"   -> [API Scout Error] {e}")
                
        elif pub_str in ["311", "wiley"]:
            print("   -> [API Scout] Bypassing Selenium. Scanning Wiley XML for data links...")
            try:
                resp = requests.get(f"https://api.wiley.com/onlinelibrary/tdm/v1/articles/{doi}", 
                                    headers={"Wiley-TDM-Client-Token": wiley_api, "Accept": "application/xml"}, timeout=20)
                if resp.status_code == 200:
                    raw_text = resp.content.decode('utf-8', errors='ignore')
                    external_links = re.findall(r'https?://(?:www\.)?(?:figshare\.com|datadryad\.org|zenodo\.org|ars\.els-cdn\.com)[^\s"\'<>\\]+', raw_text)
            except Exception as e: 
                print(f"   -> [API Scout Error] {e}")
                api_failed = True

        external_links = [link.rstrip('".<>)') for link in set(external_links) if link]

        # If API found nothing (or crashed), deploy the Selenium Scout
        if not external_links:
            print("   -> [Scout] Deploying Armored Browser to scan for external links...")
            external_links = scan_for_external_repos(doi)
            
        # Process the results
        if external_links == "Failed":
             repo_paths = ["Failed"]
        elif external_links:
            print(f"   -> [Scout] Found {len(external_links)} external links! Processing all...")
            for idx, link in enumerate(external_links):
                print(f"   -> [Scout] Processing Link {idx+1}/{len(external_links)}: {link}")
                if "datadryad.org" in link:
                    doi_match = re.search(r'(10\.\d{4,9}/[-._;()/:a-zA-Z0-9]+)', link)
                    if doi_match:
                        repo_paths.append(suppdata_dryad(doi_match.group(1), si_index=si, save_dir=save_dir))
                    else:
                        repo_paths.append(f"EXTERNAL_LINKS: {link}")
                elif "figshare" in link.lower():
                    fig_result = suppdata_figshare(link, si_index=si, save_dir=save_dir, doi=doi)  
                    # Handle multiple file paths from collection arrays gracefully
                    if isinstance(fig_result, list):
                        repo_paths.extend(fig_result)
                    else:
                        repo_paths.append(fig_result)
                elif "zenodo" in link.lower():
                    zen_result = suppdata_zenodo(link, si_index=si, save_dir=save_dir)
                    if isinstance(zen_result, list):
                        repo_paths.extend(zen_result)
                    else:
                        repo_paths.append(zen_result)
                else:
                    repo_paths.append(f"EXTERNAL_LINKS: {link}")
        else:
            print("   -> [Scout] No external repository links found.")
            repo_paths = ["No_Data"]
            
        results[doi] = {
            "publisher_file": pub_path,
            "repository_files": repo_paths
        }

    return results

# Helper 1: Formats the data into a clean multi-line string for Excel
def flatten_and_format(data):
    # THE FIX: If a raw dictionary leaks through, instantly unpack its files!
    if isinstance(data, dict):
        data = data.get("files", "No_Data")
        
    # Catch empty lists returning from failed LLM screens
    if not data or data in ["Failed", "No_Data", ["Failed"], ["No_Data"], []]:
        if isinstance(data, list) and len(data) > 0:
            return data[0]
        return "No_Data" if data == [] else data
        
    if isinstance(data, list):
        flat_list = []
        for item in data:
            # Handle nested dictionaries just in case
            if isinstance(item, dict):
                flat_list.extend([str(i) for i in item.get("files", [])])
            elif isinstance(item, list):
                flat_list.extend([str(i) for i in item])
            else:
                flat_list.append(str(item))
        return "\n".join(flat_list) if flat_list else "No_Data"
        
    return str(data)

# Helper 2: Counts ONLY successfully downloaded files
def count_successful_files(data):
    # THE FIX: If a raw dictionary leaks through, instantly unpack its files!
    if isinstance(data, dict):
        data = data.get("files", [])
        
    if not data or data in ["Failed", "No_Data", ["Failed"], ["No_Data"], []]:
        return 0
    
    count = 0
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                count += count_successful_files(item.get("files", []))
            elif isinstance(item, list):
                count += count_successful_files(item)
            else:
                item_str = str(item)
                # Ignore failures, missing data, and raw URLs that didn't download
                if item_str not in ["Failed", "No_Data"] and not item_str.startswith("EXTERNAL_LINKS:"):
                    count += 1
        return count
    else:
        # If it's a single successful path
        item_str = str(data)
        if item_str not in ["Failed", "No_Data"] and not item_str.startswith("EXTERNAL_LINKS:"):
            return 1
    return 0

