import os
import re
import requests
import urllib.parse
import shutil
from curl_cffi import requests as stealth_requests # THE FIX: Bypasses Cloudflare
import zipfile
import random
import json
import time
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
from seleniumbase import Driver
from util import download_file, extract_file_from_zip, get_tmpdir, get_html_safely, get_html_for_supp, get_supplementary_context, fetch_title_abstract
from llm_screening_gpt import filter_bes_links

# ==========================================
# PASTE YOUR API KEYS HERE!
# ==========================================
elsevier_api = "265d716860670c7e9828314405ef882c"
wiley_api = "37fe251b-b221-437f-b526-d3ce998ef9f7"
springer_api = "0575a98cd7673f928d2d1d07542e0217"

# ==========================================
# 1. No Armor Needed
# ==========================================
def suppdata_plos(doi, si_index="all", save_dir=None):
    if si_index == "all":
        results = []
        idx = 1
        print("   -> [PLOS] Downloading all supplementary files sequentially...")
        downloaded_paths = []
        while True:
            si_str = f"{idx:03d}"
            file_url = f"https://doi.org/{doi}.s{si_str}"
            res = download_file(file_url, doi, str(idx), save_dir)
            if res in ["No_Data", "Failed", None]:
                break
            downloaded_paths.append(res)
            idx += 1
            
        if not downloaded_paths: return "No_Data"
        
        # --- LLM GATEKEEPER (Post-Download Purge) ---
        title, abstract = fetch_title_abstract(doi)
        context_text = f"Title: {title}\nAbstract: {abstract}"
        potential_links = {p: os.path.basename(p) for p in downloaded_paths}
        
        print("   -> [LLM] Screening downloaded PLOS files against BES criteria...")
        approved_paths = filter_bes_links(potential_links, context_text)
        
        for p in downloaded_paths:
            if p not in approved_paths:
                try: os.remove(p)
                except: pass
        return approved_paths if approved_paths else "No_Data"
    else:
        si_str = f"{int(si_index):03d}"
        file_url = f"https://doi.org/{doi}.s{si_str}"
        return download_file(file_url, doi, str(si_index), save_dir)

def suppdata_epmc(doi, si_index="all", save_dir=None):
    url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/search/query={doi}"
    try:
        root = ET.fromstring(requests.get(url, timeout=15).content)
        pmcid_element = root.find(".//pmcid")
    except Exception:
        return "Failed"
    
    if pmcid_element is None or not pmcid_element.text: return "No_Data"
    
    dl_url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid_element.text}/supplementaryFiles"
    check_resp = requests.get(dl_url, timeout=30)
    if "errorBean" in check_resp.text[:100] or "not open access" in check_resp.text:
        print("   -> [EPMC] Paper is restricted by publisher (Not Open Access). Bypassing EPMC...")
        return "No_Data"
        
    zip_path = download_file(dl_url, doi, "raw_zip", save_dir)
    if zip_path in ["No_Data", "Failed"] or not zip_path: return "No_Data"
    
    extracted_folder = extract_file_from_zip(zip_path, si_index, get_tmpdir(save_dir))
    
    # --- LLM GATEKEEPER & FLATTENER ---
    if os.path.isdir(extracted_folder) and si_index == "all":
        title, abstract = fetch_title_abstract(doi)
        context_text = f"Title: {title}\nAbstract: {abstract}"
        extracted_files = os.listdir(extracted_folder)
        potential_links = {f: f for f in extracted_files} 
        
        print("   -> [LLM] Screening extracted EPMC files against BES criteria...")
        approved_files = filter_bes_links(potential_links, context_text)
        
        # 1. Purge the junk
        for f in extracted_files:
            if f not in approved_files:
                try: os.remove(os.path.join(extracted_folder, f))
                except: pass
                
        # 2. Check if anything survived
        surviving_files = os.listdir(extracted_folder)
        if len(surviving_files) == 0:
            print("   -> [LLM Gatekeeper] All files were rejected. Removing empty folder.")
            time.sleep(1) 
            try: shutil.rmtree(extracted_folder, ignore_errors=True)
            except: pass
            return "No_Data"
            
        # 3. THE FLATTENER: Move and rename the surviving files!
        final_paths = []
        safe_doi = str(doi).replace("/", "_").replace("\\", "_")
        
        for idx, file_name in enumerate(surviving_files, start=1):
            old_path = os.path.join(extracted_folder, file_name)
            _, ext = os.path.splitext(file_name) # Get the extension (e.g., .csv)
            
            # Create the clean, standardized name
            new_name = f"{safe_doi}_epmc_{idx}{ext}"
            new_path = os.path.join(save_dir, new_name)
            
            # Move it to the main directory
            shutil.move(old_path, new_path)
            final_paths.append(new_path)
            print(f"   -> [Flatten] Moved and renamed to: {new_name}")
            
        # 4. Delete the temporary extraction folder
        time.sleep(1)
        try: shutil.rmtree(extracted_folder, ignore_errors=True)
        except: pass
        
        # Return the list of renamed files so your Excel sheet logs them perfectly!
        return final_paths
        
    return extracted_folder

def suppdata_peerj(doi, si_index="all", save_dir=None):
    try:
        cr_data = requests.get(f"https://api.crossref.org/works/{doi}", timeout=15).json()
        xml_url = next((l['URL'] for l in cr_data['message']['link'] if l['content-type'] == 'application/xml'), None)
        if not xml_url: return "No_Data"
        
        root = ET.fromstring(requests.get(xml_url, timeout=15).content)
        supp_nodes = root.findall(".//supplementary-material")
        if not supp_nodes: return "No_Data"
        
        if si_index == "all":
            supp_links = []
            for idx, node in enumerate(supp_nodes):
                href = node.attrib.get('{http://www.w3.org/1999/xlink}href') or node.attrib.get('href')
                if href: supp_links.append(href)
                
            # --- LLM GATEKEEPER ---
            title, abstract = fetch_title_abstract(doi)
            context_text = f"Title: {title}\nAbstract: {abstract}"
            print("   -> [LLM] Screening PeerJ files against BES criteria...")
            approved_urls = filter_bes_links(supp_links, context_text)
            
            results = []
            for idx, href in enumerate(approved_urls):
                results.append(download_file(href, doi, f"supp-{idx+1}", save_dir))
            return results
        else:
            si_id = f"supp-{si_index}" if isinstance(si_index, int) else si_index
            supp_node = root.find(f".//supplementary-material[@id='{si_id}']")
            if supp_node is None: return "No_Data"
            href = supp_node.attrib.get('{http://www.w3.org/1999/xlink}href') or supp_node.attrib.get('href')
            return download_file(href, doi, si_id, save_dir)
    except Exception as e:
        print(f"   -> [PeerJ Error] {e}")
        return "Failed"

# ==========================================
# 2. REST APIs 
# ==========================================
def suppdata_elsevier(doi, si_index="all", save_dir=None):
    print("   -> [API] Routing to Elsevier API for Supplementary Data...")
    url = f"https://api.elsevier.com/content/article/doi/{doi}"


    # --- FALLBACK ---
    print("   -> [Elsevier Fallback] API unavailable/blocked. Deploying Armored Browser...")
    html = get_html_for_supp(doi)
    if not html or html == "Failed": return "Failed"
    try:
        soup = BeautifulSoup(html, "html.parser")
        supp_links = []
        for a_tag in soup.find_all('a', href=True):
            href = a_tag['href']
            text = a_tag.get_text().lower()
            if '/science/article/pii/' in href and ('/pdtt/' in href or 'suppl' in href.lower() or 'attachment' in href.lower()):
                if href not in supp_links: supp_links.append(href)
        cms_matches = re.findall(r'(/cms/[^"\'\s<>]+/attachment/[^"\'\s<>]+)', html)
        for match in cms_matches:
            if not ('/gr' in match.lower() and match.lower().endswith('.jpg')):
                if match not in supp_links: supp_links.append(match)
        if not supp_links:
            for a_tag in soup.find_all('a', href=True):
                href = a_tag['href']
                text = a_tag.get_text().lower()
                if '/cms/attachment/' in href or 'supplemental' in text or 'document s' in text or 'table s' in text:
                    if not href.startswith('#') and 'mailto:' not in href:
                        if href not in supp_links: supp_links.append(href)
        if not supp_links:
            valid_extensions = ('.zip', '.xlsx', '.xls', '.csv', '.tsv', '.ods', '.txt', '.txt.gz')
            for a_tag in soup.find_all('a', href=True):
                href = a_tag['href']
                text = a_tag.get_text().lower()
                if href.lower().endswith(valid_extensions) and ('download' in href.lower() or 'supplement' in text):
                    if not href.startswith('#'):
                        if href not in supp_links: supp_links.append(href)
        if supp_links:
            if si_index == "all":
                context_text = get_supplementary_context(html)
                full_supp_links = []
                for target_url in supp_links:
                    if target_url.startswith('/'):
                        base = "https://www.cell.com" if "cell.com" in html else "https://www.sciencedirect.com"
                        target_url = f"{base}{target_url}"
                    full_supp_links.append(target_url)
                print("   -> [LLM] Screening Elsevier Fallback files against BES criteria...")
                approved_urls = filter_bes_links(full_supp_links, context_text)
                results = []
                for idx, target_url in enumerate(approved_urls):
                    print(f"   -> [Elsevier Fallback] Downloading approved file {idx+1} of {len(approved_urls)}...")
                    results.append(download_file(target_url, doi, str(idx+1), save_dir))
                return results
            elif len(supp_links) >= si_index:
                target_url = supp_links[si_index - 1]
                if target_url.startswith('/'):
                    base = "https://www.cell.com" if "cell.com" in html else "https://www.sciencedirect.com"
                    target_url = f"{base}{target_url}"
                return download_file(target_url, doi, str(si_index), save_dir)
        else: print("   -> [Elsevier Fallback] Browser loaded page, but no supplement links exist.")
    except Exception as e: print(f"   -> [Elsevier Fallback Error] {e}")
    return "Failed"
#def suppdata_elsevier(doi, si_index="all", save_dir=None):
#    print("   -> [Elsevier] Deploying Browser...")
    
#    html = get_html_safely(f"https://doi.org/{doi}", doi) 
    
#    if not html or html == "Failed": 
#        return "Failed"
        
#    supplements = {}    
    
#    try:
#       article_url = f"https://www.cell.com/action/showPdf?pii=dummy"

#        try:
#            resolver = requests.get(
#                f"https://doi.org/{doi}",
#                allow_redirects=True,
#                timeout=15,
#                headers={
#                    "User-Agent": "Mozilla/5.0"
#                }
#            )

#            article_url = resolver.url
#
#        except Exception:
#            pass
#
#        session = stealth_requests.Session(impersonate="chrome120")
#
#        # Force trust through Cell
#        session.get("https://www.cell.com", timeout=15)
#
#        raw_response = session.get(
#            article_url,
#            timeout=20,
#            headers={
#                "Referer": "https://www.cell.com",
#                "User-Agent": "Mozilla/5.0"
#            }
#        )
#        search_text = html + " " + raw_response.text
#    except Exception:
#        search_text = html
    
    # 1. Grab the ars.els-cdn.com links (The missing CSVs!)
#    cdn_links = re.findall(r'https?://ars\.els-cdn\.com[^\s"\'<>\\]+', search_text)
#    for link in cdn_links:
#        clean_link = link.rstrip('".<>)')
#        if not clean_link.lower().endswith(('.pdf', '.docx', '.doc', '.jpg', '.jpeg', '.png')):
#            supplements[clean_link] = "Elsevier_CDN_Data_File"
        
    # 2. Grab standard ScienceDirect mmc links
#    sd_links = re.findall(r'https?://(?:www\.)?sciencedirect\.com[^\s"\'<>\\]+/(?:suppl|mmc)[^\s"\'<>\\]*', search_text)
#    for link in sd_links:
#        clean_link = link.rstrip('".<>)')
#        if not clean_link.lower().endswith(('.pdf', '.docx', '.doc', '.jpg', '.jpeg', '.png')):
#            supplements[clean_link] = "ScienceDirect_Data_File"

#    cell_links = re.findall(r'(?:https?://(?:www\.)?cell\.com)?/cms/[^\s"\'<>\\]+/attachment/[^\s"\'<>\\]+', search_text)
#    for link in cell_links:
#        clean_link = link.rstrip('".<>)')
        
        # Handle relative links (e.g., if it just starts with /cms/ instead of https://...)
#        if clean_link.startswith('/'): 
#            clean_link = f"https://www.cell.com{clean_link}"
            
        # Ignore PDFs and Word docs to protect the database
#        if not clean_link.lower().endswith(('.pdf', '.docx', '.doc', '.jpg', '.jpeg', '.png')):
#            supplements[clean_link] = clean_link

#    if not supplements:
#        print("   -> [Elsevier Fallback] Browser loaded page, but no supplement links exist.")
#        return "No_Data"
        
#    print(f"   -> [Elsevier Fallback] Found {len(supplements)} hidden links! Sending to LLM...")
    
#    if si_index == "all":
#        title, abstract = fetch_title_abstract(doi)
        
        # THE FIX: Run the scraped HTML through your context extractor!
#        extracted_context = get_supplementary_context(search_text)
#        
#        # Feed the combined mega-context to the LLM
#        context_text = f"Title: {title}\nAbstract: {abstract}\nPaper Context: {extracted_context}"
#        
#        approved_urls = filter_bes_links(supplements, context_text)
#        
#        results = []
#        
#        for idx, target_url in enumerate(approved_urls):
#            # Download the file
#            downloaded_path = download_file(target_url, doi, f"s{idx+1}", save_dir)
#            
#            # THE FIX: Check if it's a ZIP! If it is, unpack it automatically.
#            if downloaded_path and downloaded_path.endswith('.zip'):
#                print(f"   -> [Elsevier] ZIP detected. Unpacking...")
#                downloaded_path = extract_file_from_zip(downloaded_path, "all", save_dir)
#                
#            results.append(downloaded_path)
#            
#        valid_results = [r for r in results if r and r != "Failed"]
#        return valid_results if valid_results else ["No_Data"]

def suppdata_wiley(doi, si_index="all", save_dir=None):
    print("   -> [API] Routing to Wiley TDM API for Supplementary Data...")
    url = f"https://api.wiley.com/onlinelibrary/tdm/v1/articles/{doi}"
    headers = {"Wiley-TDM-Client-Token": wiley_api, "Accept": "application/xml"}
    try:
        response = requests.get(url, headers=headers, timeout=20)
        if response.status_code == 200:
            raw_text = response.content.decode('utf-8', errors='ignore')
            context_text = get_supplementary_context(raw_text)
            matches = re.findall(r'downloadSupplement\?doi=[^"\'\s<>&]+', raw_text)
            supp_links = []
            for m in matches:
                clean_link = f"https://onlinelibrary.wiley.com/action/{m}".replace('&amp;', '&')
                if clean_link not in supp_links: supp_links.append(clean_link)
            if supp_links:
                if si_index == "all":
                    print("   -> [LLM] Screening Wiley API files against BES criteria...")
                    approved_urls = filter_bes_links(supp_links, context_text)
                    results = []
                    for idx, target_url in enumerate(approved_urls):
                        print(f"   -> [Wiley API] Downloading approved file {idx+1} of {len(approved_urls)}...")
                        results.append(download_file(target_url, doi, str(idx+1), save_dir))
                    return results
                elif len(supp_links) >= si_index:
                    target_url = supp_links[si_index - 1]
                    return download_file(target_url, doi, str(si_index), save_dir)
            else: print("   -> [Wiley API] No supplementary links found in raw XML.")
    except Exception as e: print(f"   -> [Wiley API Error] {e}")

    # --- FALLBACK ---
    print("   -> [Wiley Fallback] API missed it. Deploying Armored HTML Scanner...")
    html = get_html_for_supp(doi)
    if not html or html == "Failed": return "Failed"
    soup = BeautifulSoup(html, "html.parser")
    true_base_url = "https://onlinelibrary.wiley.com"
    canonical = soup.find('link', rel='canonical')
    if canonical and canonical.get('href'):
        parsed_uri = urllib.parse.urlparse(canonical['href'])
        true_base_url = f"{parsed_uri.scheme}://{parsed_uri.netloc}"
    supp_links = []
    matches = re.findall(r'downloadSupplement\?doi=[^"\'\s<>]+', html)
    for m in matches:
        full_url = f"{true_base_url}/action/{m}".replace('&amp;', '&')
        if full_url not in supp_links: supp_links.append(full_url)
    if not supp_links:
        for a_tag in soup.find_all('a', href=True):
            href = a_tag['href']
            text = a_tag.get_text(separator=' ', strip=True).lower()
            if 'suppl' in text or 'supporting' in text or 'appendix' in text:
                if href.lower().endswith(('.pdf', '.doc', '.docx', '.xls', '.xlsx', '.zip', '.csv')) or 'download' in href.lower() or 'file' in href.lower():
                    if not href.startswith('http'): href = f"{true_base_url}{href}"
                    if href not in supp_links: supp_links.append(href)
    if supp_links:
        if si_index == "all":
            context_text = get_supplementary_context(html)
            print("   -> [LLM] Screening Wiley Fallback files against BES criteria...")
            approved_urls = filter_bes_links(supp_links, context_text)
            
            results = []
            
            # --- FIX 1: Initialize the tracking variable to a safe default ---
            downloaded_path = None
            
            for idx, target_url in enumerate(approved_urls):
                downloaded_path = download_file(target_url, doi, f"s{idx+1}", save_dir)
            
                # --- FIX 2: Move processing and appending inside the active loop ---
                if downloaded_path and downloaded_path != "Failed":
                    if downloaded_path.endswith('.zip'):
                        print(f"   -> [Wiley] ZIP detected. Unpacking...")
                        downloaded_path = extract_file_from_zip(downloaded_path, "all", save_dir)
                    
                    results.append(downloaded_path)

            # --- FIX 3: Return safely if the gatekeeper excluded everything ---
            if not results:
                print("   -> [Wiley Fallback] All available files were excluded by the LLM filter.")
                return "No_Data"
                
            return results

        elif len(supp_links) >= si_index: 
            target_url = supp_links[si_index - 1]
            return download_file(target_url, doi, str(si_index), save_dir)
    return "No_Data"
    
def suppdata_figshare(identifier, si_index="all", save_dir=None, doi=None):
    # ==========================================
    # SCENARIO A: The Private Viewing Portal (/s/)
    # ==========================================
    if "figshare.com/s/" in identifier:
        print("   -> [Figshare] Private viewing portal detected. Deploying browser to render UI...")
        token = identifier.rstrip('/').split('/')[-1]
        driver = None
        
        try:
            driver = Driver(uc=True, headless=True)
            driver.get(identifier)
            time.sleep(6)
            html_text = driver.page_source
            
            match = re.search(r'figshare\.com/ndownloader/(articles|files)/(\d+)', html_text)
            
            if match:
                item_type = match.group(1) 
                item_id = match.group(2)   
                
                dl_url = f"https://figshare.com/ndownloader/{item_type}/{item_id}?private_link={token}"
                safedoi = doi if doi else "figshare"
                
                # --- SAFELY GRAB THE FILENAME FROM VISIBLE TEXT ---
                file_desc = f"Private Figshare {item_type}"
                try:
                    soup = BeautifulSoup(html_text, 'html.parser')
                    page_title = soup.title.get_text(strip=True) if soup.title else "Figshare Dataset"
                    
                    # Destroy hidden code blocks so the regex ignores CSS/JS classes like ".r"
                    for script in soup(["script", "style"]):
                        script.decompose()
                        
                    visible_text = soup.get_text(separator=' ')
                    ext_match = re.search(r'([a-zA-Z0-9_\-\(\)]+(?: [a-zA-Z0-9_\-\(\)]+)*\.(?:xlsx|xls|csv|zip|txt|pdf|R|py))', visible_text, re.IGNORECASE)
                    
                    if ext_match:
                        file_desc = f"Dataset: {page_title} | File: {ext_match.group(1)}"
                    else:
                        file_desc = page_title
                except: 
                    pass

                # --- THE LLM GATEKEEPER ---
                title, abstract = fetch_title_abstract(safedoi)
                context_text = f"Title: {title}\nAbstract: {abstract}"
                print(f"   -> [LLM] Screening Figshare private link ({file_desc}) against BES criteria...")
                
                approved_urls = filter_bes_links({dl_url: file_desc}, context_text)
                
                if not approved_urls:
                    print("   -> [LLM Gatekeeper] File rejected.")
                    return "No_Data"
                    
                result_path = download_file(approved_urls[0], safedoi, "1", save_dir)
                
                if result_path and result_path.endswith('.zip'):
                    return extract_file_from_zip(result_path, si_index, save_dir)
                return [result_path] if si_index == "all" else result_path
            else:
                print("   -> [Figshare Error] Could not find download button. Link may be expired!")
        
        except Exception as e:
            print(f"   -> [Figshare Error] Failed to resolve private portal: {e}")
        finally:
            if driver:
                try: driver.quit()
                except: pass
                
        # THE DEAD-LINK SAFETY NET
        if doi:
            print(f"   -> [Figshare Fallback] Private link failed. Hunting for public data using paper DOI: {doi}...")
            try:
                search_url = "https://api.figshare.com/v2/articles/search"
                search_payload = {"search_for": doi}
                search_resp = requests.post(search_url, json=search_payload, timeout=15)
                
                if search_resp.status_code == 200:
                    results = search_resp.json()
                    if results and len(results) > 0:
                        public_article_id = results[0]['id']
                        
                        files_resp = requests.get(f"https://api.figshare.com/v2/articles/{public_article_id}/files", timeout=15)
                        files = files_resp.json()
                        safedoi = doi if doi else "figshare"
                        
                        if files:
                            if si_index == "all":
                                title, abstract = fetch_title_abstract(safedoi)
                                context_text = f"Title: {title}\nAbstract: {abstract}"
                                potential_links = {f.get('download_url'): f.get('name') for f in files}
                                
                                print("   -> [LLM] Screening Figshare Fallback API files...")
                                approved_urls = filter_bes_links(potential_links, context_text)
                                
                                dl_results = []
                                for idx, dl_url in enumerate(approved_urls):
                                    dl_results.append(download_file(dl_url, safedoi, str(idx+1), save_dir))
                                return dl_results
                            elif len(files) >= si_index:
                                target_file = files[si_index - 1]
                                return download_file(target_file.get('download_url'), safedoi, str(si_index), save_dir)
            except Exception as e:
                print(f"   -> [Figshare Fallback Error] API Search failed: {e}")
                
        return "Failed"

    # ==========================================
    # SCENARIO B: Direct Download Links (/ndownloader/)
    # ==========================================
    elif "figshare.com/ndownloader/" in identifier:
        safedoi = doi if doi else "figshare"
        
        title, abstract = fetch_title_abstract(safedoi)
        context_text = f"Title: {title}\nAbstract: {abstract}"
        print("   -> [LLM] Screening Figshare direct link against BES criteria...")
        
        approved_urls = filter_bes_links({identifier: "Direct Figshare Download"}, context_text)
        
        if not approved_urls:
            print("   -> [LLM Gatekeeper] File rejected.")
            return "No_Data"
            
        result_path = download_file(approved_urls[0], safedoi, "1", save_dir)
        
        if result_path and result_path.endswith('.zip'):
            return extract_file_from_zip(result_path, si_index, save_dir)
        return [result_path] if si_index == "all" else result_path

    # ==========================================
    # SCENARIO C: Standard Public Links & Collections (The API Route)
    # ==========================================
    article_id = None
    collection_id = None
    safedoi = doi if doi else "figshare"

    # Step 1: Resolve DOI or get landing URL redirection
    if "figshare.com" in identifier:
        final_url = identifier
    else:
        try:
            url = f"https://doi.org/{identifier}" if not identifier.startswith('http') else identifier
            response = requests.get(url, allow_redirects=True, timeout=15)
            final_url = response.url
        except Exception:
            return "Failed"

    # Step 2: Route by Figshare Asset Type (Collection vs Article)
    if "/articles/" in final_url.lower():
        match = re.search(r'articles/.*?/(\d+)', final_url)
        if not match: match = re.search(r'/(\d+)(?:/v\d+)?$', final_url.rstrip('/'))
        if match: article_id = match.group(1)
    elif "/collections/" in final_url.lower() or ".figshare.c." in final_url.lower():
        # Captures explicit collection paths or collection-style DOIs
        match = re.search(r'collections/.*?/(\d+)', final_url)
        if not match: match = re.search(r'\.c\.(\d+)', final_url)
        if not match: match = re.search(r'/(\d+)(?:/v\d+)?$', final_url.rstrip('/'))
        if match: collection_id = match.group(1)
    else:
        # Generic fallback string pattern matching
        match = re.search(r'/(\d+)(?:/v\d+)?$', final_url.rstrip('/'))
        if match: article_id = match.group(1)

    # Step 3: Extract Underlying Article IDs
    target_article_ids = []
    try:
        if collection_id:
            print(f"   -> [Figshare] Collection detected (ID: {collection_id}). Querying internal articles...")
            coll_api = f"https://api.figshare.com/v2/collections/{collection_id}/articles"
            coll_resp = requests.get(coll_api, timeout=15)
            if coll_resp.status_code == 200:
                target_article_ids = [str(item['id']) for item in coll_resp.json()]
            print(f"   -> [Figshare] Extracted {len(target_article_ids)} articles from the collection container.")
        elif article_id:
            target_article_ids = [article_id]
        else:
            return "Failed"

        # Step 4: Gather all raw file elements across all target articles
        all_candidate_files = []
        for art_id in target_article_ids:
            files_api = f"https://api.figshare.com/v2/articles/{art_id}/files"
            files_resp = requests.get(files_api, timeout=15)
            if files_resp.status_code == 200:
                all_candidate_files.extend(files_resp.json())

        if not all_candidate_files: 
            return "No_Data"

        # Step 5: Filter files by target tabular extension formats
        valid_extensions = ('.csv', '.tsv', '.txt', '.xlsx', '.xls')
        filtered_potential_links = {}
        
        for f in all_candidate_files:
            file_name = f.get('name', '')
            download_url = f.get('download_url')
            
            if download_url and file_name.lower().endswith(valid_extensions):
                filtered_potential_links[download_url] = file_name

        if not filtered_potential_links:
            print(f"   -> [Figshare] Found {len(all_candidate_files)} files, but 0 matched tabular extensions {valid_extensions}.")
            return "No_Data"

        print(f"   -> [Figshare] Identified {len(filtered_potential_links)} matching tabular data files for verification.")

        # Step 6: Submit to the LLM Validation Gatekeeper
        if si_index == "all":
            title, abstract = fetch_title_abstract(safedoi)
            context_text = f"Title: {title}\nAbstract: {abstract}"
            
            print("   -> [LLM] Screening filtered Figshare data files against BES criteria...")
            approved_urls = filter_bes_links(filtered_potential_links, context_text)
            
            results = []
            for idx, dl_url in enumerate(approved_urls):
                print(f"   -> [Figshare Download] Extracting confirmed file {idx+1} of {len(approved_urls)}...")
                results.append(download_file(dl_url, safedoi, str(idx+1), save_dir))
            return results
            
        elif si_index == "first" or si_index == 1:
            # Fallback for handling specific selection index integers safely
            first_url = list(filtered_potential_links.keys())[0]
            return download_file(first_url, safedoi, "1", save_dir)

    except Exception as e:
        print(f"   -> [Figshare System Error] API extraction workflow crashed: {e}")
        return "Failed"
        
    return "Failed"

def suppdata_zenodo(doi_or_url, si_index="all", save_dir=None):
    """
    Robust Zenodo handler:
    - API-first (prevents 403)
    - Fully armored get_html_safely fallback HTML scraping to bypass Cloudflare/403 blocks
    - Safe error collection returning "Failed" on terminal 403 errors
    """
    print(f"   -> [Zenodo] Processing: {doi_or_url}")

    # ==========================================
    # STEP 1 — RESOLVE DOI
    # ==========================================
    try:
        if "doi.org" in str(doi_or_url):
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, Gecko) Chrome/122.0 Safari/537.36"
            }
            r = requests.get(
                doi_or_url,
                headers=headers,
                allow_redirects=True,
                timeout=15
            )
            resolved_url = r.url
        else:
            resolved_url = str(doi_or_url)
    except Exception as e:
        print(f"   -> [Resolver Error] {e}")
        resolved_url = str(doi_or_url)

    # ==========================================
    # STEP 2 — EXTRACT RECORD ID
    # ==========================================
    record_id = resolved_url.rstrip("/").split("/")[-1].replace("zenodo.", "")

    if not record_id.isdigit():
        print("   -> [Zenodo] Invalid record ID")
        return "Failed", "Invalid Zenodo Record ID"

    print(f"   -> [Zenodo] Record ID: {record_id}")

    # ==========================================
    # STEP 3 — TRY OFFICIAL API FIRST
    # ==========================================
    api_url = f"https://zenodo.org/api/records/{record_id}"
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, Gecko) Chrome/122.0 Safari/537.36",
        "Accept": "application/json",
        "Accept-Language": "en-GB,en;q=0.9"
    })

    supplements = None
    try:
        api_resp = session.get(api_url, timeout=20)
        if api_resp.status_code == 200:
            data = api_resp.json()
            supplements = {}
            for f in data.get("files", []):
                file_url = f.get("links", {}).get("self")
                filename = f.get("key", "zenodo_file")
                if file_url:
                    supplements[file_url] = filename
            print(f"   -> [Zenodo API] Found {len(supplements)} files")
        elif api_resp.status_code == 403:
            print("   -> [Zenodo API] API request hit a 403 block. Advancing to Armored Fallback...")
        else:
            print(f"   -> [Zenodo API] Non-200 Status Code ({api_resp.status_code}). Advancing to HTML Fallback...")
    except Exception as e:
        print(f"   -> [Zenodo API Error] {e}")

    # ==========================================
    # STEP 4 — ARMORED FALLBACK SCRAPE (FIXED)
    # ==========================================
    if supplements is None:
        record_url = f"https://zenodo.org/records/{record_id}"
        print(f"   -> [Zenodo Fallback] Deploying get_html_safely armor for Zenodo Record UI...")
        
        # FIX: Routing through your existing armored framework instead of a raw requests loop
        html = get_html_safely(record_url, doi=None)
        
        if not html or html == "Failed":
            print("   -> [Zenodo] Terminal 403/Anti-Bot Block encountered across all network levels.")
            return "Failed", "HTTP 403 Forbidden: Armored Scraping Blocked by Provider"

        soup = BeautifulSoup(html, "html.parser")
        supplements = {}
        script = soup.find("script", id="record-files-data")

        if script:
            try:
                files = json.loads(script.string)
                for f in files.get("files", []):
                    url = f.get("links", {}).get("content")
                    name = f.get("key", "zenodo_file")
                    if url:
                        supplements[url] = name
            except Exception as e:
                print(f"   -> [Parse Error] {e}")

    # ==========================================
    # STEP 5 — OUTPUT CHECK
    # ==========================================
    if not supplements:
        print("   -> [Zenodo] No files found inside dataset containers")
        return "No_Data"

    print(f"   -> [Zenodo] Total files: {len(supplements)}")

    # ==========================================
    # STEP 6 — RETURN / DOWNLOAD
    # ==========================================
    results = []
    urls = list(supplements.keys())

    if si_index == "all":
        targets = urls
    elif isinstance(si_index, int):
        if 1 <= si_index <= len(urls):
            targets = [urls[si_index - 1]]
        else:
            return "Failed", "Requested index out of range"
    else:
        return "Failed", "Invalid index filter configuration"

    for i, url in enumerate(targets):
        results.append(
            download_file(
                url,
                f"zenodo_{record_id}",
                f"s{i+1}",
                save_dir
            )
        )

    return results

# ==========================================
# 3. HTML SCRAPERS (Fully Armored)
# ==========================================
def suppdata_science(doi, si_index="all", save_dir=None):
    url1 = f"https://www.sciencemag.org/lookup/doi/{doi}"
    html1 = get_html_safely(url1, doi)
    if not html1 or html1 == "Failed": return "Failed"
    matches1 = re.findall(r'(/content/)[0-9/]*', html1)
    if matches1:
        url2 = f"https://www.sciencemag.org{matches1[0]}/suppl/DC1"
        html2 = get_html_safely(url2, doi)
        if not html2 or html2 == "Failed": return "Failed"
        matches2 = re.findall(r'(/content/suppl/)[A-Z0-9/\.]*', html2)
        unique_links = list(dict.fromkeys(matches2))
        if unique_links:
            if si_index == "all":
                title, abstract = fetch_title_abstract(doi)
                context_text = f"Title: {title}\nAbstract: {abstract}"
                full_urls = [f"https://www.sciencemag.org{match}" for match in unique_links]
                print("   -> [LLM] Screening Science files against BES criteria...")
                approved_urls = filter_bes_links(full_urls, context_text)
                results = []
                for idx, final_url in enumerate(approved_urls):
                    results.append(download_file(final_url, doi, str(idx+1), save_dir))
                return results
            elif len(unique_links) >= int(si_index):
                final_url = f"https://www.sciencemag.org{unique_links[int(si_index)-1]}"
                return download_file(final_url, doi, str(si_index), save_dir)
    return "No_Data"

def suppdata_nature(doi, si_index="all", save_dir=None):
    """
    Scrapes Nature.com directly for supplementary media files.
    Bypasses Zenodo by finding the direct publisher links.
    """
    print(f"   -> [Nature] Scraping article page for DOI: {doi}...")
    
    # Clean the DOI to get the article ID (e.g., ncomms10122)
    article_id = str(doi).split('/')[-1]
    url = f"https://www.nature.com/articles/{article_id}"
    
    # Use our stealth browser to get the HTML
    html = get_html_safely(url, doi)
    if not html or html == "Failed":
        print("   -> [Nature] Failed to retrieve HTML.")
        return "Failed"
        
    soup = BeautifulSoup(html, "html.parser")
    supplements = {}
    
    # Hunt for Nature's unique media links
    for a_tag in soup.find_all('a', href=True):
        href = a_tag['href']
        text = a_tag.get_text(strip=True) or "Nature_Supplementary_File"
        
        # THE FIX: Broaden the search words for Nature's messy file structures!
        href_lower = href.lower()
        is_file = any(ext in href_lower for ext in ['.zip', '.xlsx', '.xls', '.csv', '.tsv', '.txt'])
        is_media = any(keyword in href_lower for keyword in ['/media/', 'mediaobjects', '/files/', '/esm/', 'supplementary'])
        
        if is_media or is_file:
            full_url = urllib.parse.urljoin("https://www.nature.com", href)
            supplements[full_url] = text
            
    if not supplements:
        print("   -> [Nature] No supplementary media links found directly on the page.")
        return "No_Data"
        
    if si_index == "all":
        # Fetch context for the LLM Bouncer
        title, abstract = fetch_title_abstract(doi)
        context_text = f"Title: {title}\nAbstract: {abstract}"
        
        print("   -> [LLM] Screening Nature files against BES criteria...")
        approved_urls = filter_bes_links(supplements, context_text)
        
        results = []
        for idx, target_url in enumerate(approved_urls):
            results.append(download_file(target_url, doi, f"s{idx+1}", save_dir))
            
        # Filter out None/Failed results
        valid_results = [r for r in results if r and r != "Failed"]
        return valid_results if valid_results else ["No_Data"]
        
    else:
        urls = list(supplements.keys())
        if isinstance(si_index, int) and 0 < si_index <= len(urls):
            return download_file(urls[si_index - 1], doi, f"s{si_index}", save_dir)
        return "Failed"


def suppdata_pnas(doi, si_index="all", save_dir=None):
    url = f"https://www.pnas.org/doi/suppl/{doi}"
    html = get_html_safely(url, doi)
    if not html or html == "Failed": return "Failed"
    soup = BeautifulSoup(html, "html.parser")
    supp_links = []
    for a_tag in soup.find_all('a', href=True):
        if '/suppl_file/' in a_tag['href']:
            if a_tag['href'] not in supp_links: supp_links.append(a_tag['href'])
    if supp_links:
        if si_index == "all":
            title, abstract = fetch_title_abstract(doi)
            context_text = f"Title: {title}\nAbstract: {abstract}"
            full_urls = [f"https://www.pnas.org{u}" if u.startswith('/') else u for u in supp_links]
            print("   -> [LLM] Screening PNAS files against BES criteria...")
            approved_urls = filter_bes_links(full_urls, context_text)
            results = []
            for idx, full_url in enumerate(approved_urls):
                results.append(download_file(full_url, doi, str(idx+1), save_dir))
            return results
        elif len(supp_links) >= int(si_index):
            target_href = supp_links[int(si_index) - 1]
            full_url = f"https://www.pnas.org{target_href}" if target_href.startswith('/') else target_href
            return download_file(full_url, doi, str(si_index), save_dir)
    return "No_Data"

def suppdata_proceedings(doi, si_index="all", vol=None, issue=None, save_dir=None):
    try:
        journal = re.findall(r"(rsp)[a-z]", doi)[0]
        tail = re.findall(r"[0-9]+\.[0-9]*", doi)[1].replace(".", "")
        url = f"https://{journal}.royalsocietypublishing.org/content/{vol}/{issue}/{tail}.figures-only"
        html = get_html_safely(url, doi)
        if not html or html == "Failed": return "Failed"
        matches = re.findall(r"(highwire/filestream)[a-zA-Z0-9_/\.]*", html)
        unique_links = list(dict.fromkeys(matches))
        if unique_links:
            if si_index == "all":
                title, abstract = fetch_title_abstract(doi)
                context_text = f"Title: {title}\nAbstract: {abstract}"
                full_urls = [f"https://rspb.royalsocietypublishing.org/{m}" for m in unique_links]
                print("   -> [LLM] Screening Proceedings files against BES criteria...")
                approved_urls = filter_bes_links(full_urls, context_text)
                results = []
                for idx, final_url in enumerate(approved_urls):
                    results.append(download_file(final_url, doi, str(idx+1), save_dir))
                return results
            elif len(unique_links) >= int(si_index):
                final_url = f"https://rspb.royalsocietypublishing.org/{unique_links[int(si_index)-1]}"
                return download_file(final_url, doi, str(si_index), save_dir)
        return "No_Data"
    except Exception: return "Failed"

def suppdata_biorxiv(doi, si_index="all", save_dir=None):
    try:
        resp = requests.get(f"https://doi.org/{doi}", headers={'User-Agent': 'Mozilla'}, timeout=15)
        html = get_html_safely(resp.url + ".figures-only", doi)
        if not html or html == "Failed": return "Failed"
        matches = re.findall(r'/highwire/filestream/[a-z0-9A-Z\./_-]*', html)
        unique_links = list(dict.fromkeys(matches))
        if unique_links:
            if si_index == "all":
                title, abstract = fetch_title_abstract(doi)
                context_text = f"Title: {title}\nAbstract: {abstract}"
                full_urls = [f"https://www.biorxiv.org{m}" for m in unique_links]
                print("   -> [LLM] Screening BioRxiv files against BES criteria...")
                approved_urls = filter_bes_links(full_urls, context_text)
                results = []
                for idx, final_url in enumerate(approved_urls):
                    results.append(download_file(final_url, doi, str(idx+1), save_dir))
                return results
            elif len(unique_links) >= int(si_index):
                final_url = f"https://www.biorxiv.org{unique_links[int(si_index)-1]}"
                return download_file(final_url, doi, str(si_index), save_dir)
        return "No_Data"
    except Exception: return "Failed"

def suppdata_dryad(doi, si_index="all", save_dir=None):
    html = get_html_safely(f"https://doi.org/{doi}", doi)
    if not html or html == "Failed": return "Failed"
    
    soup = BeautifulSoup(html, "html.parser")
    supp_links = []
    
    # Hunt specifically for Dryad dataset download buttons
    for a_tag in soup.find_all('a', href=True):
        if '/api/v2/datasets/' in a_tag['href'] and '/download' in a_tag['href']:
            if a_tag['href'] not in supp_links: supp_links.append(a_tag['href'])
            
    # Fallback hunt
    if not supp_links:
        for a_tag in soup.find_all('a', href=True, title=True):
            if a_tag['title'].isdigit() and a_tag['href'] not in supp_links: supp_links.append(a_tag['href'])

    if supp_links:
        # Dryad links download the entire dataset as a single ZIP file
        dl_url = f"https://datadryad.org{supp_links[0]}" if not supp_links[0].startswith('http') else supp_links[0]
        
        # 1. Download the ZIP dataset
        zip_save_name = f"{doi.split('/')[1]}-dryad"
        zip_path = download_file(dl_url, doi, custom_name=zip_save_name, save_dir=save_dir)
        if zip_path in ["No_Data", "Failed"] or not zip_path: return "No_Data"
        
        # 2. Extract it to a temporary folder
        extracted_folder = extract_file_from_zip(zip_path, "all", get_tmpdir(save_dir))
        
        # --- LLM GATEKEEPER & FLATTENER ---
        if os.path.isdir(extracted_folder):
            title, abstract = fetch_title_abstract(doi)
            context_text = f"Title: {title}\nAbstract: {abstract}"
            
            # Grab all the files inside the Dryad dataset
            extracted_files = os.listdir(extracted_folder)
            potential_links = {f: f for f in extracted_files} 
            
            print("   -> [LLM] Screening extracted Dryad files against BES criteria...")
            approved_files = filter_bes_links(potential_links, context_text)
            
            # 1. Purge the junk (R scripts, ReadMes, etc.)
            for f in extracted_files:
                if f not in approved_files:
                    try: os.remove(os.path.join(extracted_folder, f))
                    except: pass
                    
            # 2. Check if any data survived
            surviving_files = os.listdir(extracted_folder)
            if len(surviving_files) == 0:
                print("   -> [LLM Gatekeeper] All files were rejected. Removing empty folder.")
                time.sleep(1)
                try: shutil.rmtree(extracted_folder, ignore_errors=True)
                except: pass
                return "No_Data"
                
            # 3. THE FLATTENER: Move and rename surviving data files!
            final_paths = []
            safe_doi = str(doi).replace("/", "_").replace("\\", "_")
            
            for idx, file_name in enumerate(surviving_files, start=1):
                old_path = os.path.join(extracted_folder, file_name)
                
                # Skip nested directories if any exist
                if os.path.isdir(old_path): continue 
                
                _, ext = os.path.splitext(file_name)
                
                new_name = f"{safe_doi}_dryad_{idx}{ext}"
                new_path = os.path.join(save_dir, new_name)
                
                shutil.move(old_path, new_path)
                final_paths.append(new_path)
                print(f"   -> [Flatten] Moved and renamed to: {new_name}")
                
            # 4. Delete the temporary folder
            time.sleep(1)
            try: shutil.rmtree(extracted_folder, ignore_errors=True)
            except: pass
            
            return final_paths
            
    return "No_Data"

def suppdata_copernicus(doi, si_index="all", save_dir=None):
    html = get_html_safely(f"https://doi.org/{doi}", doi)
    if not html or html == "Failed": return "Failed"
    soup = BeautifulSoup(html, "html.parser")
    a_tag = soup.find('a', string="Supplement")
    if not a_tag: return "No_Data"
    dl_url = a_tag.get('href')
    
    if dl_url.endswith('.zip'):
        zip_save_name = f"{doi.split('/')[1]}-supplement"
        zip_path = download_file(dl_url, doi, custom_name=zip_save_name, save_dir=save_dir)
        if zip_path in ["No_Data", "Failed"] or not zip_path: return "No_Data"
        extracted_folder = extract_file_from_zip(zip_path, "all", get_tmpdir(save_dir))
        
        # --- LLM GATEKEEPER & FLATTENER ---
        if os.path.isdir(extracted_folder):
            title, abstract = fetch_title_abstract(doi)
            context_text = f"Title: {title}\nAbstract: {abstract}"
            extracted_files = os.listdir(extracted_folder)
            potential_links = {f: f for f in extracted_files} 
            print("   -> [LLM] Screening Copernicus extracted files against BES criteria...")
            approved_files = filter_bes_links(potential_links, context_text)
            
            # 1. Purge the junk
            for f in extracted_files:
                if f not in approved_files:
                    try: os.remove(os.path.join(extracted_folder, f))
                    except: pass
                    
            # 2. Check if anything survived
            surviving_files = os.listdir(extracted_folder)
            if len(surviving_files) == 0:
                print("   -> [LLM Gatekeeper] All files were rejected. Removing empty folder.")
                time.sleep(1)
                try: shutil.rmtree(extracted_folder, ignore_errors=True)
                except: pass
                return "No_Data"
                
            # 3. THE FLATTENER: Move and rename the surviving files
            final_paths = []
            safe_doi = str(doi).replace("/", "_").replace("\\", "_")
            
            for idx, file_name in enumerate(surviving_files, start=1):
                old_path = os.path.join(extracted_folder, file_name)
                _, ext = os.path.splitext(file_name)
                
                new_name = f"{safe_doi}_copernicus_{idx}{ext}"
                new_path = os.path.join(save_dir, new_name)
                
                shutil.move(old_path, new_path)
                final_paths.append(new_path)
                print(f"   -> [Flatten] Moved and renamed to: {new_name}")
                
            # 4. Delete the temp folder
            time.sleep(1)
            try: shutil.rmtree(extracted_folder, ignore_errors=True)
            except: pass
            
            return final_paths

def suppdata_mdpi(doi, si_index="all", save_dir=None):
    try:
        cr_data = requests.get(f"https://api.crossref.org/works/{doi}").json()
        pdf_url = next((l['URL'] for l in cr_data['message']['link'] if l['URL'].endswith('/pdf')), None)
        if not pdf_url: return "No_Data"
        base_url = pdf_url[:-3]
        html = get_html_safely(base_url, doi)
        if not html or html == "Failed": return "Failed"
        soup = BeautifulSoup(html, "html.parser")
        supp_links = []
        for a_tag in soup.find_all('a', href=True):
            if re.search(r's\d+$', a_tag['href']) and base_url in a_tag['href']:
                if a_tag['href'] not in supp_links: supp_links.append(a_tag['href'])
        if not supp_links: supp_links = [f"{base_url}s1"]
        if si_index == "all":
            title, abstract = fetch_title_abstract(doi)
            context_text = f"Title: {title}\nAbstract: {abstract}"
            print("   -> [LLM] Screening MDPI files against BES criteria...")
            approved_urls = filter_bes_links(supp_links, context_text)
            results = []
            for idx, url in enumerate(approved_urls):
                res = download_file(url, doi, f"s{idx+1}", save_dir)
                if res not in ["No_Data", "Failed", None]: results.append(res)
            return results if results else "No_Data"
        else:
            return download_file(f"{base_url}s{si_index}", doi, f"s{si_index}", save_dir)
    except Exception: return "Failed"

def suppdata_jstatsoft(doi, si_index="all", save_dir=None):
    html = get_html_safely(f"https://doi.org/{doi}", doi)
    if not html or html == "Failed": return "Failed"
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find('table', class_='supplementfiles')
    if not table: return "No_Data"
    supplements = []
    for row in table.find_all('tr'):
        cells = row.find_all('td')
        if len(cells) >= 3:
            a_tag = cells[2].find('a')
            if a_tag and a_tag.get('href'): supplements.append(a_tag.get('href'))
    if supplements:
        if si_index == "all":
            title, abstract = fetch_title_abstract(doi)
            context_text = f"Title: {title}\nAbstract: {abstract}"
            print("   -> [LLM] Screening JStatSoft files against BES criteria...")
            approved_urls = filter_bes_links(supplements, context_text)
            results = []
            for idx, target_url in enumerate(approved_urls):
                results.append(download_file(target_url, doi, str(idx+1), save_dir))
            return results
        else:
            target_idx = int(si_index) if str(si_index).isdigit() else 1
            if len(supplements) >= target_idx:
                return download_file(supplements[target_idx - 1], doi, str(si_index), save_dir)
    return "No_Data"