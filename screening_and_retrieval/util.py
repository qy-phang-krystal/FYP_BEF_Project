import os
import io
import re
import mimetypes
import tempfile
import requests
from seleniumbase import Driver
from selenium.webdriver.common.by import By
import shutil
import random
import time
import zipfile
import urllib.parse
from urllib.parse import urlparse
import xml.etree.ElementTree as ET
from curl_cffi import requests as stealth_requests 
from pypdf import PdfReader
from bs4 import BeautifulSoup

# ==========================================S
# SCREEN PAPERS FOR LLM
# ==========================================

def resolve_article_and_trust_url(doi, user_agent=None):
    """
    Resolve DOI safely.

    Handles:
    - normal DOI redirects
    - chooser.crossref pages
    - publisher-family trust routing
    """

    if not user_agent:

        user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )

    headers = {
        "User-Agent": user_agent
    }

    doi_url = f"https://doi.org/{doi}"

    try:

        response = requests.get(
            doi_url,
            headers=headers,
            allow_redirects=True,
            timeout=20
        )

        article_url = response.url

    except Exception as e:

        print(f"   -> [Resolver Error] {e}")

        return {
            "article_url": doi_url,
            "trust_url": "https://www.google.com",
            "publisher_family": "unknown"
        }

    lowered = article_url.lower()

    # ==========================================
    # PUBLISHER DETECTION
    # ==========================================

    cell_patterns = [
        "cell.com",
        "/cell/",
        "/current-biology/",
        "/neuron/",
        "/immunity/",
        "/cancer-cell/",
        "/joule/",
        "/chem/",
        "/med/",
        "/iscience/"
    ]

    elsevier_patterns = [
        "sciencedirect.com",
        "elsevier.com"
    ]

    if any(x in lowered for x in cell_patterns):

        publisher_family = "cell"

        trust_url = "https://www.cell.com"

    elif any(x in lowered for x in elsevier_patterns):

        publisher_family = "elsevier"

        trust_url = "https://www.sciencedirect.com"

    else:

        publisher_family = "generic"

        parsed = urlparse(article_url)

        trust_url = (
            f"{parsed.scheme}://{parsed.netloc}"
        )

    print(
        f"   -> [Resolver] Publisher: "
        f"{publisher_family}"
    )

    print(
        f"   -> [Resolver] Trust URL: "
        f"{trust_url}"
    )

    return {
        "article_url": article_url,
        "trust_url": trust_url,
        "publisher_family": publisher_family
    }

def get_html_safely(url, doi=None):
    print(f"   -> [Armor] Attempting stealth connection to {url}...")
    
    # We initialize article_url with the base url just in case
    article_url = url 
    
    try:
        session = stealth_requests.Session(
            impersonate="chrome120"
        )

        response = session.get(
            url,
            timeout=20,
            allow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            }
        )

        # ==========================================
        # HANDLE CROSSREF CHOOSER
        # ==========================================
        if "chooser.crossref.org" in response.url.lower():
            print("   -> [Resolver] Crossref chooser detected via redirect.")
            try:
                chooser_html = response.text
                soup = BeautifulSoup(chooser_html, "html.parser")
                links = []

                for a in soup.find_all("a", href=True):
                    href = a["href"]
                    lowered = href.lower()

                    bad_patterns = [
                        "crossref.org", "mailto:", "orcid", 
                        "login", "#", "javascript:", "cookie"
                    ]
                    if any(x in lowered for x in bad_patterns):
                        continue

                    if href.startswith("http://") or href.startswith("https://"):
                        links.append(href)

                if links:
                    target_index = min(2, len(links) - 1) if len(links) > 2 else 0
                    
                    # FIX 1: Save the resolved URL to our outer scope variable 'article_url'
                    article_url = links[target_index]
                    print(f"   -> [Resolver] Selected Outbound URL:\n      {article_url}")
                    
                    # Try to fetch it via requests first
                    response = session.get(
                        article_url,
                        timeout=20,
                        allow_redirects=True,
                        headers=response.request.headers
                    )
                else:
                    print("   -> [Resolver Warning] No valid outbound targets extracted from chooser.")

            except Exception as e:
                print(f"   -> [Resolver] Chooser parse failed: {e}")

        # Processing anti-bot guards against the current page state
        html_text = response.text
        challenge_keywords = [
            "Just a moment", "Enable JavaScript", "cf-browser-verification",
            "DataDome", "Cloudflare", "captcha"
        ]

        is_blocked = response.status_code in [403, 429, 503]
        is_skeleton = len(html_text) < 4000
        has_challenge = any(x.lower() in html_text.lower() for x in challenge_keywords)

        if not (is_blocked or is_skeleton or has_challenge):
            return html_text
            
        print("   -> [Armor] Challenge detected. Launching browser...")

    except Exception as e:
        print(f"   -> [Armor Error] {e}")

    # ==========================================
    # FALLBACK BROWSER (CRITICAL FIXES HERE)
    # ==========================================
    driver = None
    try:
        driver = Driver(
            uc=True,
            uc_cdp=True,
            headless=False,
            user_data_dir="chrome_profile"
        )

        if driver is None:
            raise Exception("Driver failed to initialize")

        if doi:
            resolved = resolve_article_and_trust_url(doi)
            # Only overwrite if the resolver didn't get stuck on crossref
            if "chooser.crossref.org" not in resolved["article_url"].lower():
                article_url = resolved["article_url"]
            trust_url = resolved["trust_url"]
        else:
            parsed = urlparse(article_url)
            trust_url = f"{parsed.scheme}://{parsed.netloc}"

        # FIX 3: Force the Trust URL away from Crossref if it picked it up incorrectly
        if "crossref.org" in trust_url.lower():
            parsed_art = urlparse(article_url)
            trust_url = f"{parsed_art.scheme}://{parsed_art.netloc}"

        print(f"   -> [Armor] Establishing trust via {trust_url}")

        # Navigate to the publisher homepage to build cookie trust
        driver.get(trust_url)
        time.sleep(random.uniform(5, 8))

        try:
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight/3);")
            time.sleep(random.uniform(1.0, 2.0))
        except Exception:
            pass

        # FIX 4: Navigate directly to the real GeoScienceWorld/Publisher URL we found!
        print(f"   -> [Armor] Browser navigating directly to: {article_url}")
        driver.get(article_url)
        time.sleep(random.uniform(7, 10))
        html = driver.page_source

        return html

    except Exception as e:
        print(f"   -> [Browser Error] {e}")
        return "Failed"

    finally:
        if driver:
            try:
                driver.quit()
            except Exception:
                pass
    
def get_html_for_supp(doi):
    """
    Unified Armored Browser with Anti-Loop Bypass.
    Uses 'uc_cdp=True' to defeat ScienceDirect's tamper alarm.
    """
    
    url = f"https://doi.org/{doi}"
    print(f"   -> [Supp Armor] Launching unified stealth browser to scan {url}...")
    
    driver = None
    try:
        # 1. THE FIX: Add uc_cdp=True for maximum stealth
        driver = Driver(
            uc=True,
            uc_cdp=True,
            headless=False
        )

        if driver is None:
            raise Exception("Driver failed to initialize")
        
        # 2. THE FIX: Use standard get(). Do NOT use reconnect (it triggers the tamper alarm)
        driver.get(url)
        
        # 3. THE FIX: No robotic mouse clicks! We just wait. 
        # (Click manually if a box appears, but it usually auto-passes now)
        print("   -> [Supp Armor] Waiting 12 seconds to clear Cloudflare...")
        time.sleep(12) 
        
        print("   -> [Supp Armor] Cloudflare cleared. Hunting for hidden dropdown menus...")
        try:
            dropdowns = driver.find_elements("xpath", "//*[contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'supporting information') or contains(translate(text(), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'supplement')]")
            
            click_count = 0
            for elem in dropdowns:
                try:
                    driver.execute_script("arguments[0].click();", elem)
                    click_count += 1
                except Exception:
                    pass
            print(f"   -> [Supp Armor] Clicked {click_count} potential dropdowns to trigger Lazy Loading.")
        except Exception as e:
            print(f"   -> [Supp Armor] No dropdowns found to click.")

        print("   -> [Supp Armor] Waiting 3 seconds for links to spawn in the HTML...")
        time.sleep(3)
        
        html = driver.page_source
        driver.quit()
        return html
        
    except Exception as e:
        print(f"   -> [Supp Armor Error] {e}")
        if driver:
            try: driver.quit()
            except Exception: pass
        return "Failed"   


def get_supplementary_context(raw_xml_or_html):
    """Extracts the paragraphs describing supplementary files from the paper."""
    # Looks for Elsevier XML supplementary tags or Data Availability sections
    supp_blocks = re.findall(r'<ce:supplementary-material.*?</ce:supplementary-material>', raw_xml_or_html, re.DOTALL | re.IGNORECASE)
    data_avail = re.findall(r'<ce:data-availability.*?</ce:data-availability>', raw_xml_or_html, re.DOTALL | re.IGNORECASE)
    
    # Combine them into one string for the LLM
    context = " ".join(supp_blocks + data_avail)
    
    # If the regex misses (e.g. it's HTML, not XML), just return the first 10,000 characters 
    # of the paper to give the LLM a general idea of what the paper is about.
    if not context.strip():
        return raw_xml_or_html[:10000] 
        
    return context


def fetch_title_abstract(doi):
    """Tier 1: Fast Abstract Fetch (EPMC -> Crossref)"""
    title, abstract = None, None
    try:
        api_url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=DOI:{doi}&resultType=core&format=json"
        response = requests.get(api_url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data['hitCount'] > 0:
                paper = data['resultList']['result'][0]
                title = paper.get('title')
                abstract = paper.get('abstractText')
                if title and abstract:
                    return title, abstract
    except Exception:
        pass 

    if not abstract or abstract == "Abstract not found":
        try:
            url = f"https://api.crossref.org/works/{doi}"
            headers = {'User-Agent': 'mailto:your_email@example.com'} 
            res = requests.get(url, headers=headers, timeout=5)
            if res.status_code == 200:
                data = res.json()['message']
                if not title: 
                    title = data.get('title', ['Title not found'])[0]
                raw_abstract = data.get('abstract')
                if raw_abstract:
                    abstract = re.sub(r'<[^>]+>', '', raw_abstract)
                    return title, abstract
        except Exception:
            pass 
            
    return (title or "Title not found"), (abstract or "Abstract not found")

def fetch_full_text_xml(doi):
    """Tier 2a: Fetches raw XML full text from EPMC (Takes ~1 second)"""
    search_url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=DOI:{doi}&resultType=lite&format=json"
    try:
        search_res = requests.get(search_url, timeout=10).json()
        if search_res.get('hitCount', 0) > 0:
            pmcid = search_res['resultList']['result'][0].get('pmcid')
            if pmcid:
                xml_url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"
                xml_res = requests.get(xml_url, timeout=10)
                if xml_res.status_code == 200:
                    return xml_res.content
        return None
    except Exception:
        return None

def smart_extract_text_and_figures(xml_content):
    """Tier 2a: Filters the massive XML down to just methodology and location keywords."""
    try:
        root = ET.fromstring(xml_content)
        paragraphs = [p.text for p in root.findall(".//p") if p.text]
        captions = []
        for fig in root.findall(".//fig") + root.findall(".//table-wrap"):
            label = fig.find(".//label")
            caption_text = fig.find(".//caption//p")
            l_str = label.text if label is not None else "Item"
            c_str = caption_text.text if caption_text is not None else ""
            captions.append(f"[{l_str}] {c_str}")

        all_text = paragraphs + captions
        target_keywords = ['uk', 'united kingdom', 'england', 'scotland', 'wales', 'northern ireland', 
                           'method', 'study site', 'data', 'dataset', 'figure', 'table']
        
        condensed_text = []
        for p in all_text:
            if any(kw in p.lower() for kw in target_keywords) or p.startswith('['): 
                clean_p = re.sub(r'\s+', ' ', p).strip()
                condensed_text.append(clean_p)
                
        return " ".join(condensed_text)[:6000] # Cap at 6000 chars to save LLM RAM
    except Exception:
        return None

def extract_methods_from_html(html, current_url="https://doi.org"):
    """
    Aggressive HTML parsing. 
    Tries specific sections first, then falls back to grabbing all paragraphs.
    """
    if not html or html == "Failed": return "Methods not found"
    soup = BeautifulSoup(html, 'html.parser')
    
    for junk in soup(["nav", "footer", "header", "script", "style", "aside", "form", "button"]):
        junk.decompose()

    # Publisher-Specific Tags (Elsevier, Nature)
    # Looks for 'id' or 'class' containing method keywords
    method_keywords = ['method', 'material', 'experimental', 'procedure', 'approach']
    for tag in soup.find_all(['section', 'div']):
        tag_id = tag.get('id', '').lower()
        tag_class = ' '.join(tag.get('class', [])).lower()
        if any(kw in tag_id or kw in tag_class for kw in method_keywords):
            text = tag.get_text(separator=' ', strip=True)
            if len(text) > 500: # Ensure it's a real section, not just a tiny UI button
                print(text[:500]) # Print the first 500 chars for debugging
                return text[:50000] # Return up to 50,000 chars

    # Header Search
    method_headings = ['methods', 'materials and methods', 'methodology', 'approach', 'study design', 'experimental design']
    for heading in soup.find_all(['h1', 'h2', 'h3', 'h4', 'strong', 'b']):
        heading_text = heading.get_text().strip().lower()
        if any(mh in heading_text for mh in method_headings):
            methods_content = []
            sibling = heading.find_next_sibling()
            while sibling and sibling.name not in ['h1', 'h2', 'h3', 'h4']:
                if sibling.name in ['p', 'div', 'section']:
                    methods_content.append(sibling.get_text(separator=' ', strip=True))
                sibling = sibling.find_next_sibling()
            if methods_content:
                print(' '.join(methods_content)[:500]) # Print the first 500 chars for debugging
                return ' '.join(methods_content)[:50000]
    
    # methods not found so grab ALL paragraphs
    print("   -> [Scraper] Specific Methods section hidden. Grabbing all text")
    all_paragraphs = [p.get_text(separator=' ', strip=True) for p in soup.find_all('p')]
    
    # Filter out tiny UI elements (like "Click here to subscribe" or "Menu")
    full_text = ' '.join([p for p in all_paragraphs if len(p) > 50]) 

    if full_text:
        print(full_text[:500]) # Print the first 500 chars for debugging
        return full_text[:50000] # Cap at 50,000 chars to save LLM RAM 
        
    return "Methods section not found."


def get_publisher_from_crossref(doi):
    """
    Translates R's .suppdata.pub
    Uses the Crossref API to find the exact publisher ID of a DOI.
    """
    if "figshare" in doi.lower():
        return "figshare"
    if "dryad" in doi.lower():
        return "dryad"
        
    print(f"   -> [Crossref] Identifying publisher for DOI: {doi}...")
    url = f"https://api.crossref.org/works/{doi}"
    
    try:
        # We use a politely identified User-Agent (Crossref requests this)
        headers = {'User-Agent': 'SuppData_Python_Port/1.0 (mailto:your_email@imperial.ac.uk)'}
        response = requests.get(url, headers=headers, timeout=10)
        
        if response.status_code == 200:
            data = response.json()['message']
            member_id = data.get('member') # This is the numeric code (e.g., '340')
            if member_id:
                print(f"   -> [Crossref] Found member ID: {member_id}")
                return str(member_id)
        return "unknown"
    except Exception as e:
        print(f"   -> [Crossref Error] {e}")
        return "unknown"

def route_publisher(member_id):
    """
    Translates R's .suppdata.func (switch statement)
    Maps the numeric Crossref ID to the publisher name.
    """
    publisher_map = {
        "340": "plos",
        "311": "wiley",
        "221": "science",
        "175": "proceedings", # Royal Society
        "246": "biorxiv",
        "4443": "peerj",
        "3145": "copernicus",
        "1968": "mdpi",
        "7893": "jstatsoft"
    }
    # If the ID isn't found, fallback to Europe PMC like the R script does!
    return publisher_map.get(member_id, "epmc")


def get_file_suffix(response):
    #Check the Content-Disposition header (The most accurate)
    if 'Content-Disposition' in response.headers:
        cd = response.headers['Content-Disposition']
        # This regex robustly hunts for filename="something.doc"
        match = re.search(r'filename\*?=(?:UTF-8\'\')?[\'"]?([^\'";]+)[\'"]?', cd, re.IGNORECASE)
        if match:
            fname = match.group(1)
            _, ext = os.path.splitext(fname)
            if ext: return ext.lower()

    #Check the MIME Type (How Chrome does it!)
    # If the server says 'application/msword', mimetypes translates it to '.doc'
    if 'Content-Type' in response.headers:
        content_type = response.headers['Content-Type'].split(';')[0].strip()
        if content_type not in ['application/octet-stream', 'binary/octet-stream']:
            ext = mimetypes.guess_extension(content_type)
            # Make sure it doesn't give us weird default extensions
            if ext and ext not in ['.bin', '.obj']: 
                return ext.lower()

    # STRATEGY 3: Check the final redirected URL
    parsed_url = urllib.parse.urlparse(response.url)
    _, ext = os.path.splitext(parsed_url.path)
    if ext and len(ext) <= 5 and not re.match(r'^\.s[0-9]+$', ext.lower()):
        return ext.lower()

    # STRATEGY 4: Ultimate Fallback
    return ".zip"

def scan_for_external_repos(doi):
    url = f"https://doi.org/{doi}"
    print(f"   -> [Scout] Launching SeleniumBase Armored Browser to scan {url}...")
    
    driver = None
    try:
        driver = Driver(uc=True, headless=False) 
        if any(x in url.lower() for x in [
            "sciencedirect",
            "elsevier",
            "cell"
        ]):
            driver.get("https://www.cell.com")
            time.sleep(6)

        resolved = resolve_article_and_trust_url(doi)

        article_url = resolved["article_url"]
        trust_url = resolved["trust_url"]

        print(f"   -> [Scout] Trust URL: {trust_url}")

        # warm trust first
        driver.get(trust_url)

        time.sleep(6)

        try:
            driver.execute_script(
                "window.scrollTo(0, document.body.scrollHeight/3);"
            )

            time.sleep(1.5)

        except Exception:
            pass

        # NOW open article page
        driver.get(article_url)

        time.sleep(8)
        
        html_text = driver.page_source
        
        if "Just a moment..." in html_text:
            print("   -> [Scout Error] Cloudflare still blocked the browser.")
            return []
            
        # 1. Hunt for literal website links
        pattern1 = r'(https?://(?:www\.)?(?:datadryad\.org|figshare\.com|zenodo\.org|osf\.io)[^\s"\'<>]+)'
        matches = re.findall(pattern1, html_text)
        
        # 2. THE FIX: The Repository DOI Sniper
        # Catches links like 10.5061/dryad... or 10.6084/m9.figshare... even if they are hidden in plain text
        pattern2 = r'(10\.(?:5061/dryad|5281/zenodo|6084/m9\.figshare)[a-zA-Z0-9_/\.-]+)'
        doi_matches = re.findall(pattern2, html_text)
        
        for d in doi_matches:
            clean_doi = d.rstrip('.,;)"\'<')
            if "dryad" in clean_doi:
                matches.append(f"https://datadryad.org/stash/dataset/doi:{clean_doi}")
            else:
                # Figshare and Zenodo handle DOIs perfectly via standard redirection
                matches.append(f"https://doi.org/{clean_doi}")

        unique_links = []
        for link in matches:
            clean_link = link.rstrip('.,;)"\'<')
            if clean_link not in unique_links:
                unique_links.append(clean_link)
                
        return unique_links
        
    except Exception as e:
        print(f"   -> [Scout Error] Browser crashed: {e}")
        return []
    finally:
        if driver:
            try: driver.quit()
            except: pass

# ==========================================
# 3. DIRECTORY & FILENAME HELPERS
# Translates .tmpdir and .save.name
# ==========================================

def get_tmpdir(directory=None):
    if directory is not None:
        if not os.path.exists(directory):
            raise ValueError(f"'dir' {directory} must exist unless None")
        return directory
    return tempfile.gettempdir()

def get_save_name(doi, save_name=None, file_identifier="1"):
    if save_name is None:
        # Replace slashes so your computer doesn't think it's a folder path
        safe_doi = str(doi).replace("/", "_").replace("\\", "_")
        save_name = f"{safe_doi}_s{file_identifier}"
    return save_name



#def download_file(url, doi, file_identifier="1", save_dir=None, custom_name=None, cache=True, suffix=None):
#    final_dir = get_tmpdir(save_dir)
#    base_name = get_save_name(doi, custom_name, file_identifier)
    
#    print(f"   -> [Download] Connecting to {url}")
    
#    need_browser = False
#    response = None
#    trust_url = f"https://doi.org/{doi}" # Absolute fallback
    
    # --- PHASE 1: The Fast Request (Upgraded with Trust Context) ---
#    try:
#        from curl_cffi import requests as stealth_requests
        
        # Create a session to hold Cloudflare/DataDome cookies!
#        session = stealth_requests.Session(impersonate="chrome110")
        
        # We secretly resolve the DOI redirect using our invisible stealth_requests session.
        # This prevents Selenium from hitting the heavily-guarded doi.org redirect gateway!
#        print("   -> [Download] Resolving final article URL silently...")
#        res = session.get(f"https://doi.org/{doi}", timeout=15)
#        trust_url = res.url 
        
        # If the file belongs to Cell Press, the cell.com homepage is the easiest trust anchor.
#        if "cell.com" in url.lower():
#            trust_url = "https://www.cell.com"
            
#        print(f"   -> [Download] Establishing trust context via {trust_url}...")
#        session.get(trust_url, timeout=15)
        
        # Add human-like headers so Cell Press/Elsevier don't think we are a hotlinking bot
#        headers = {
#            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
#            "Referer": trust_url,
#            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
#        }
        
        # Use the trusted session to fetch the file
#        response = session.get(url, headers=headers, stream=True, timeout=15, allow_redirects=True)
        
#        content_type = response.headers.get('Content-Type', '').lower()
        
#        if response.status_code in [403, 503, 429] or 'text/html' in content_type:
#            print(f"   -> [Download] Interception detected! (Status: {response.status_code}, Type: {content_type})")
#            need_browser = True
#            if response:
#                response.close()
            
#    except Exception as e:
#        if "redirect" in str(e).lower() or "maximum" in str(e).lower():
#            print("   -> [Download] Redirect loop/Security trap detected!")
#            need_browser = True
#            if "cell.com" in url.lower(): trust_url = "https://www.cell.com"
#        else:
#            print(f"   -> [Download Error] {e}")
#           return "Failed"

    # --- PHASE 2: The Armored Browser (If Trapped) ---
#    if need_browser:
#        if "api.elsevier.com" in url or "api.wiley.com" in url:
#            print("   -> [Download] API endpoint blocked. Fast-failing to trigger HTML Fallback...")
#            return "Failed"
            
#        print("   -> [Download] Deploying Armored Browser to bypass trap...")
#        driver = None
#        try:
#            import time
#            import base64
#            import shutil
#            from seleniumbase import Driver
            
            # We skip the PDF Heist for Cell Press so it uses the JS Clicker instead
#            is_pdf = (".pdf" in url.lower() or suffix == ".pdf") and "cell.com" not in url.lower() and "sciencedirect.com" not in url.lower()
            
            # THE FIX: uc_cdp=True is explicitly required to stop ScienceDirect's infinite refresh loop!
#            driver = Driver(uc=True, uc_cdp=True, headless=False)
            
#            if is_pdf:
                # PATH A: The Base64 Memory Heist (For standard static PDFs)
#                print("   -> [Download] PDF detected. Using Memory Heist...")
#                print(f"   -> [Download] Pre-loading to acquire security cookies...")
                
                # THE FIX: Because of uc_cdp=True, we use standard get(). 
                # Reconnecting drops CDP and triggers ScienceDirect's tamper alarm!
#                driver.get(trust_url)
#                try:
#                    driver.uc_gui_click_captcha()
#                except Exception:
#                    pass
#                time.sleep(12) 
                
#                driver.get(url)
#                print("   -> [Download] Waiting 15s for render...")
#                time.sleep(15) 
                
#               driver.set_script_timeout(30)
#                base64_data = driver.execute_async_script("""
#                    var url = window.location.href;
#                    var callback = arguments[arguments.length - 1];
#                    fetch(url)
#                        .then(response => response.blob())
#                        .then(blob => {
#                            var reader = new FileReader();
#                            reader.onloadend = function() { callback(reader.result.split(',')[1]); }
#                            reader.readAsDataURL(blob);
#                        }).catch(error => callback("ERROR: " + error.message));
#                """)
                
#                if base64_data and not base64_data.startswith("ERROR"):
#                    pdf_bytes = base64.b64decode(base64_data)
#                    dest = os.path.join(final_dir, f"{base_name}.pdf")
#                    with open(dest, "wb") as f: f.write(pdf_bytes)
#                    print(f"   -> [Success] Heist complete! Saved to: {dest}")
#                    return dest
#                else:
#                    print(f"   -> [Download Error] Memory extraction failed.")
#                    return "Failed"
#            else:
                # PATH B: The Session Hijack (Cookie Transfer)
#                print("   -> [Download] Native File detected. Deploying Session Hijack...")
                
#                print(f"   -> [Download] Loading {trust_url} to establish trust context...")
                
#                driver.get(trust_url)
#                try:
#                    driver.uc_gui_click_captcha()
#                except Exception:
#                    pass
#                time.sleep(12) # Wait for Cloudflare/DataDome to clear
                
#                print("   -> [Download] Trust established. Extracting security cookies...")
#                selenium_cookies = driver.get_cookies()
#                session_cookies = {cookie['name']: cookie['value'] for cookie in selenium_cookies}
#                user_agent = driver.execute_script("return navigator.userAgent;")
#                current_url = driver.current_url
                
#                print("   -> [Download] Hijacking session to download file directly...")
                
                # THE FIX: We use stealth_requests with TLS impersonation instead of standard requests!
#                from curl_cffi import requests as stealth_requests
                
#                dl_headers = {
#                    "User-Agent": user_agent,
#                    "Referer": current_url,
#                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
#                }
                
                # Pass the stolen browser cookies back to stealth_requests
#                dl_response = stealth_requests.get(
#                    url, 
#                    headers=dl_headers, 
#                    cookies=session_cookies, 
#                    stream=True, 
#                    timeout=30,
#                    impersonate="chrome110" # Matches your Session setup at the top of the script
#                )
                
#                if dl_response.status_code in [200, 202]:
#                    if not suffix:
#                        try:
#                            import urllib.parse
#                            _, ext = os.path.splitext(urllib.parse.urlparse(dl_response.url).path)
#                            suffix = ext.lower() if ext else ".zip"
#                        except:
#                            suffix = ".zip"
                            
#                    dest = os.path.join(final_dir, f"{base_name}{suffix}")
                    
#                    with open(dest, "wb") as f:
#                        for chunk in dl_response.iter_content(chunk_size=8192):
#                            if chunk: f.write(chunk)
                            
#                    print(f"   -> [Success] Session Hijack complete! Saved to: {dest}")
#                    return dest
#                else:
#                    print(f"   -> [Download Error] Hijack blocked (Status {dl_response.status_code}).")
#                    return "Failed"
                    
#        except Exception as e:
#            print(f"   -> [Download Error] Armored Browser failed: {e}")
#            return "Failed"
#        finally:
#            if driver:
#               try: driver.quit()
#                except: pass

    # --- PHASE 3: Standard Fast Download (If no firewall/redirects exist) ---
#    else:
#        try:
#            if response.status_code == 404:
#                return "No_Data"
                
#           response.raise_for_status() 
            
#            if not suffix:
#                try:
#                    import urllib.parse
#                    parsed_url = urllib.parse.urlparse(response.url)
#                    _, ext = os.path.splitext(parsed_url.path)
#                    suffix = ext.lower() if ext else ".zip"
#                except:
#                    suffix = ".zip"
            
#            dest = os.path.join(final_dir, f"{base_name}{suffix}")

#            if cache and os.path.exists(dest):
#                return dest

#            with open(dest, "wb") as f:
#                for chunk in response.iter_content(chunk_size=8192):
#                    if chunk: f.write(chunk)
#                        
#            print(f"   -> [Success] File written to {dest}")
#            return dest
#        except Exception as e:
#            print(f"   -> [Error] File writing failed: {e}")
#            return "Failed"

def transfer_driver_cookies(driver, session):

    """
    Copy Selenium cookies into requests session.
    Essential for Cell/Elsevier attachment downloads.
    """

    selenium_cookies = driver.get_cookies()

    for cookie in selenium_cookies:

        try:

            session.cookies.set(
                cookie['name'],
                cookie['value'],
                domain=cookie.get('domain'),
                path=cookie.get('path')
            )

        except Exception:
            pass

    return session

def download_file( 
        url, 
        doi, 
        file_identifier="1", 
        save_dir=None, 
        custom_name=None, 
        cache=True, 
        suffix=None ): 
        
    final_dir = get_tmpdir(save_dir) 
    base_name = get_save_name( doi, custom_name, file_identifier ) 
    
    resolved = resolve_article_and_trust_url(doi) 
    article_url = resolved["article_url"] 
    trust_url = resolved["trust_url"] 
    
    print(f" -> [Download] Trust URL: {trust_url}") 
    
    try: 
        session = stealth_requests.Session( impersonate="chrome120" ) 
        # warm session first 
        session.get( 
            trust_url, 
            timeout=20, 
            headers={ 
                "User-Agent": ( 
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) " 
                    "AppleWebKit/537.36 (KHTML, like Gecko) " 
                    "Chrome/120.0.0.0 Safari/537.36" ) } ) 
        
        time.sleep(random.uniform(1.0, 2.5)) 
        
        response = session.get( 
            url, 
            stream=True, 
            allow_redirects=True, 
            timeout=30, 
            headers={ 
                "Referer": article_url, 
                "User-Agent": ( 
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) " 
                    "AppleWebKit/537.36 (KHTML, like Gecko) " 
                    "Chrome/120.0.0.0 Safari/537.36" ) } ) 
        
        blocked = ( response.status_code in [403, 429, 503] ) 
        if blocked: 
            raise Exception("Browser fallback required") 
        
        if not suffix: 
            suffix = get_file_suffix(response) 

        destination = os.path.join( final_dir, f"{base_name}{suffix}" ) 
        
        with open(destination, "wb") as f: 
            for chunk in response.iter_content(8192): 
                if chunk: 
                    f.write(chunk) 
                    
        print(f" -> [Download] Saved to {destination}") 
        
        return destination 
    
    except Exception as e: 
        print(f" -> [Download Fallback] {e}") 
    

    # ==========================================
    # BROWSER FALLBACK
    # ==========================================

    driver = None

    try:

        driver = Driver(
            uc=True,
            uc_cdp=True,
            headless=False,
            user_data_dir="chrome_profile"
        )

        if driver is None:

            raise Exception(
                "Driver failed to initialize"
            )

        # ==========================================
        # ESTABLISH TRUST
        # ==========================================

        driver.get(trust_url)

        time.sleep(random.uniform(5, 8))

        try:

            driver.execute_script(
                "window.scrollTo("
                "0,"
                "document.body.scrollHeight/3"
                ");"
            )

            time.sleep(random.uniform(1, 2))

        except Exception:
            pass

        # ==========================================
        # OPEN ARTICLE PAGE
        # ==========================================

        print(
            "   -> [Download] "
            "Opening article page..."
        )

        driver.get(article_url)

        time.sleep(random.uniform(6, 10))

        # ==========================================
        # OPEN ATTACHMENT
        # ==========================================

        print(
            "   -> [Download] "
            "Opening attachment in browser..."
        )

        driver.get(url)

        time.sleep(random.uniform(8, 12))

        # ==========================================
        # CHECK CALLBACK FAILURE
        # ==========================================

        current_url = driver.current_url.lower()

        if "/callback?" in current_url:

            raise Exception(
                "Cell callback redirect loop detected"
            )

        # ==========================================
        # TRANSFER AUTHENTICATED COOKIES
        # ==========================================

        session = requests.Session()

        session = transfer_driver_cookies(
            driver,
            session
        )

        headers = {
            "Referer": article_url,
            "User-Agent": (
                "Mozilla/5.0 "
                "(Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/120.0.0.0 "
                "Safari/537.36"
            )
        }

        response = session.get(
            url,
            stream=True,
            allow_redirects=True,
            timeout=40,
            headers=headers
        )

        if response.status_code >= 400:

            raise Exception(
                f"HTTP {response.status_code}"
            )

        blocked = (
            response.status_code
            in [403, 429, 503]
        )

        if blocked:

            raise Exception(
                "Browser-assisted download blocked"
            )

        # ==========================================
        # DETERMINE FILE SUFFIX
        # ==========================================

        if not suffix:

            try:

                suffix = get_file_suffix(
                    response
                )

            except Exception:

                suffix = ".dat"

        destination = os.path.join(
            final_dir,
            f"{base_name}{suffix}"
        )

        with open(destination, "wb") as f:

            for chunk in response.iter_content(
                8192
            ):

                if chunk:

                    f.write(chunk)

        print(
            f"   -> [Download] "
            f"Saved browser-assisted file "
            f"to {destination}"
        )

        return destination

    except Exception as e:

        print(
            f"   -> [Download Error] {e}"
        )

        return "Failed"

    finally:

        if driver:

            try:
                driver.quit()

            except Exception:
                pass


def extract_file_from_zip(zip_path, si_index, save_dir, new_name=None):
    try:
        base_name = os.path.splitext(os.path.basename(zip_path))[0]
        extract_folder = os.path.join(save_dir, f"{base_name}_data")
        
        # 1. THE FIX: Test if it's actually a valid ZIP before creating any folders!
        if not zipfile.is_zipfile(zip_path):
            raise zipfile.BadZipFile("File is not a valid zip archive (likely a paywall error page).")
            
        # 2. Make the folder only after confirming the zip is safe
        os.makedirs(extract_folder, exist_ok=True)
        
        with zipfile.ZipFile(zip_path, 'r') as z:
            z.extractall(extract_folder)
            
        print(f"   -> [Success] Extracted contents to: {extract_folder}")
        
        try: os.remove(zip_path)
        except: pass
            
        # 3. NESTED ZIP CATCHER
        for item in os.listdir(extract_folder):
            if item.lower().endswith('.zip'):
                nested_zip_path = os.path.join(extract_folder, item)
                print(f"   -> [Unzip] Found a nested ZIP ({item}). Extracting it...")
                try:
                    with zipfile.ZipFile(nested_zip_path, 'r') as nz:
                        nz.extractall(extract_folder)
                    os.remove(nested_zip_path) 
                except Exception as e:
                    print(f"   -> [Warning] Could not extract nested zip: {e}")
        
        return extract_folder
        
    except Exception as e:
        print(f"   -> [Unzip Error] Failed to extract archive: {e}")
        # THE FIX: Clean up the corrupted ZIP and any empty folders!
        try: os.remove(zip_path)
        except: pass
        try: shutil.rmtree(extract_folder, ignore_errors=True)
        except: pass
        return "Failed"

elsevier_api = "265d716860670c7e9828314405ef882c"
wiley_api = "37fe251b-b221-437f-b526-d3ce998ef9f7"
springer_api = "0575a98cd7673f928d2d1d07542e0217"

def get_elsevier_api_text(doi):
    """Fetches the full text directly from Elsevier's servers."""
    print(f"   -> [API] Routing to Elsevier TDM API...")
    url = f"https://api.elsevier.com/content/article/doi/{doi}"
    headers = {
        "Accept": "text/xml", # Ask for pure XML, not a webpage!
        "X-ELS-APIKey": elsevier_api
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            # We use BeautifulSoup just to strip away the XML tags and leave pure text
            from bs4 import BeautifulSoup
            clean_text = BeautifulSoup(response.text, "xml").get_text(separator=' ', strip=True)
            return clean_text[:20000] # Cap at 20k characters for GPT
        else:
            print(f"   -> [API Error] Elsevier returned status {response.status_code}")
            return "Failed"
    except Exception as e:
        print(f"   -> [API Error] {e}")
        return "Failed"
    

def get_wiley_api_text(doi):
    """Fetches text directly from Wiley's TDM API."""
    print(f"   -> [API] Routing to Wiley TDM API...")
    
    # Wiley's specific API endpoint for full-text extraction
    url = f"https://api.wiley.com/onlinelibrary/tdm/v1/articles/{doi}"
    
    
    headers = {
        "Wiley-TDM-Client-Token": wiley_api,
        "Accept": "application/xml" 
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=15)
        
        if response.status_code == 200:
            # Check WHAT Wiley actually sent us
            content_type = response.headers.get('Content-Type', '').lower()
            
            # SCENARIO A: Wiley sent a PDF
            if 'application/pdf' in content_type:
                print("   -> [API] Wiley sent a PDF! Extracting text in-memory...")
                # Load the binary PDF data into memory
                pdf_file = io.BytesIO(response.content)
                reader = PdfReader(pdf_file)
                
                pdf_text = ""
                # Loop through the pages and grab the text
                for page in reader.pages:
                    extracted = page.extract_text()
                    if extracted:
                        pdf_text += extracted + " "
                
                return pdf_text[:20000] # Cap for GPT-4o-mini
                
            # SCENARIO B: Wiley sent XML/HTML
            else:
                print("   -> [API] Wiley sent XML text. Parsing...")
                from bs4 import BeautifulSoup
                clean_text = BeautifulSoup(response.text, "html.parser").get_text(separator=' ', strip=True)
                return clean_text[:20000]
                
        else:
            print(f"   -> [API Error] Wiley returned status {response.status_code}")
            return "Failed"
            
    except Exception as e:
        print(f"   -> [API Error] {e}")
        return "Failed"
    
def get_springer_api_text(doi):
    """Fetches text from Springer Nature."""
    print(f"   -> [API] Routing to Springer API...")
    # Springer passes the key right in the URL
    url = f"http://api.springernature.com/openaccess/json?q=doi:{doi}&api_key={springer_api}"
    
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            # Navigate Springer's JSON to find the abstract/body
            if 'records' in data and len(data['records']) > 0:
                paragraphs = data['records'][0].get('abstract', '')
                
                return paragraphs[:20000]
        return "Failed"
    except Exception:
        return "Failed"
    
def llm_filter_and_download(
    doi: str,
    candidate_urls: list,
    descriptions: list = None,
    save_dir: str = "downloads",
    publisher: str = "unknown",
):
    from llm_screening_gpt import filter_bes_links

    if not candidate_urls:
        return {
            "status": "failed",
            "reason": "No candidate URLs found",
            "files": [],
        }

    if descriptions is None:
        descriptions = [""] * len(candidate_urls)

    try:
        title, abstract = fetch_title_abstract(doi)

        context_text = (
            f"Title: {title}\n"
            f"Abstract: {abstract}"
        )

    except Exception as e:
        return {
            "status": "failed",
            "reason": f"Context fetch failed: {e}",
            "files": [],
        }

    try:
        approved_urls = filter_bes_links(
            context_text=context_text,
            urls=candidate_urls,
            descriptions=descriptions,
               )

    except Exception as e:
        return {
            "status": "failed",
            "reason": f"LLM filtering failed: {e}",
            "files": [],
        }

    downloaded_files = []

    for url in approved_urls:
        try:
            path = download_file(
                url=url,
                doi=doi,
                save_dir=save_dir,
            )

            if path:
                downloaded_files.append(path)

        except Exception as e:
            print(f"[!] Download failed: {url}")
            print(e)

    if not downloaded_files:
        return {
            "status": "failed",
            "reason": "No files downloaded",
            "files": [],
        }

    return {
        "status": "success",
        "publisher": publisher,
        "files": downloaded_files,
    }