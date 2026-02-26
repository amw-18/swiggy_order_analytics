import os
import json
import mailbox
import email.utils
import datetime
import re
import io
from pypdf import PdfReader
from bs4 import BeautifulSoup
from collections import defaultdict

MBOX_PATH = "export.mbox"
OUT_DIR = "segregated_orders"

def extract_address(html_payload):
    soup = BeautifulSoup(html_payload, "html.parser")
    address_str = None
    
    # Strategy 1: "Delivery To:"
    delivery_to_p = soup.find(lambda tag: tag.name == "p" and "Delivery To:" in tag.text)
    if delivery_to_p:
        td = delivery_to_p.find_parent("td")
        address_parts = []
        for element in td.find_all(['p', 'h5']):
            text = element.get_text(strip=True)
            if text and text != "Delivery To:":
                address_parts.append(text)
        address_str = ", ".join(address_parts)
        
    # Strategy 2: Text extraction ("Deliver To:" pattern)
    if not address_str:
        lines = soup.get_text(separator='\n', strip=True).split('\n')
        for i_line, line in enumerate(lines):
            if "Deliver To:" in line:
                if i_line + 1 < len(lines):
                    address_str = lines[i_line+1]
                    if len(address_str) > 5:
                        break
                    
    if address_str:
        return address_str.replace('\r', '').replace('\n', ' ').strip()
    return None

def extract_amount(html_payload):
    soup = BeautifulSoup(html_payload, "html.parser")
    
    # Try finding grand-total or Order Total
    for el in soup.find_all(['td', 'th', 'span']):
        text = el.get_text(strip=True)
        if "Order Total:" in text or "Grand Total" in text:
            parent_tr = el.find_parent('tr')
            if parent_tr:
                text_in_tr = parent_tr.get_text(separator=' ', strip=True)
                
                # Try specific regex first to avoid catching earlier prices in the same tr
                match = re.search(r'(?:Order Total:|Grand Total)\s*₹\s*([0-9.,]+)', text_in_tr)
                if match:
                    return match.group(1).replace(',', '')
                
                # If specific doesn't match for some reason, maybe they are separated differently?
                next_td = el.find_next_sibling(['td', 'th'])
                if next_td:
                    match = re.search(r'₹\s*([0-9.,]+)', next_td.get_text(strip=True))
                    if match:
                        return match.group(1).replace(',', '')
    
    # Fallback to plain text search
    lines = soup.get_text(separator='\n', strip=True).split('\n')
    for i, line in enumerate(lines):
        if "Order Total:" in line or "Grand Total" in line:
            # Check the same line first
            match = re.search(r'(?:Order Total:|Grand Total)\s*₹\s*([0-9.,]+)', line)
            if match:
                return match.group(1).replace(',', '')
                
            for j in range(i, min(i+5, len(lines))):
                match = re.search(r'₹\s*([0-9.,]+)', lines[j])
                if match:
                    return match.group(1).replace(',', '')
                    
    return None

def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    mbox = mailbox.mbox(MBOX_PATH)
    
    address_to_folder = {}
    folder_counter = 1
    
    # address_folder -> YYYYMMDD -> count
    date_email_counts = defaultdict(lambda: defaultdict(int))
    # address_folder -> YYYYMMDD -> {"eml1": amount, ...}
    daily_summaries = defaultdict(lambda: defaultdict(dict))
    
    # address_folder -> YYYY-MM -> amount
    monthly_summaries = defaultdict(lambda: defaultdict(float))
    
    print(f"Processing {len(mbox)} emails from {MBOX_PATH}...")
    
    for i, msg in enumerate(mbox):
        subject = msg.get("Subject", "")
        subject_lower = subject.lower()
        if not (subject_lower.startswith("your swiggy order") or 
                subject_lower.startswith("your swiggy gourmet order") or 
                subject_lower.startswith("your instamart order")):
            continue
            
        html_payload = None
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/html":
                    html_payload = part.get_payload(decode=True).decode("utf-8", errors="replace")
                    break
        else:
            if msg.get_content_type() == "text/html":
                html_payload = msg.get_payload(decode=True).decode("utf-8", errors="replace")
                
        address = extract_address(html_payload) if html_payload else "Unknown Address"
        
        # We still extract an HTML amount as a fallback
        amount_str = extract_amount(html_payload) if html_payload else "0.0"
        amount = float(amount_str) if amount_str else 0.0
        pdf_amount = None
        
        # Address Folder Mapping
        if address not in address_to_folder:
            if address == "Unknown Address":
                address_folder = "unknown_address"
            else:
                address_folder = f"address{folder_counter}"
                folder_counter += 1
            address_to_folder[address] = address_folder
        else:
            address_folder = address_to_folder[address]
            
        # Date processing
        date_header = msg.get("Date")
        parsed_date = email.utils.parsedate_to_datetime(date_header) if date_header else None
        
        date_folder = parsed_date.strftime("%Y%m%d") if parsed_date else "Unknown_Date"
        month_key = parsed_date.strftime("%Y-%m") if parsed_date else "Unknown_Date"
        
        target_dir = os.path.join(OUT_DIR, address_folder, date_folder)
        os.makedirs(target_dir, exist_ok=True)
        
        # Counters
        date_email_counts[address_folder][date_folder] += 1
        eml_index = date_email_counts[address_folder][date_folder]
        eml_label = f"eml{eml_index}"
        
        # Save exact raw email bytes
        eml_path = os.path.join(target_dir, f"{eml_index}.eml")
        with open(eml_path, "wb") as f:
            f.write(msg.as_bytes())
            
        # Process attachments
        attach_idx = 0
        if msg.is_multipart():
            for part in msg.walk():
                content_type = part.get_content_type()
                if "application/" in content_type or "image/" in content_type:
                    filename = part.get_filename() or "attachment"
                    ext = ""
                    if "." in filename:
                        ext = "." + filename.split('.')[-1]
                    
                    attach_idx += 1
                    attach_name = f"{eml_label}attach{attach_idx}{ext}"
                    
                    attach_path = os.path.join(target_dir, attach_name)
                    payload = part.get_payload(decode=True)
                    if payload:
                        with open(attach_path, "wb") as f:
                            f.write(payload)
                            
                        # If PDF, attempt to read the amount directly and override HTML amount
                        if "pdf" in ext.lower() or "application/pdf" in content_type:
                            try:
                                reader = PdfReader(io.BytesIO(payload))
                                text = ""
                                for page in reader.pages:
                                    extracted = page.extract_text()
                                    if extracted:
                                        text += extracted + "\n"
                                match = re.search(r'(?:Invoice Total|Invoice Value)\s*([0-9.,]+)', text, re.IGNORECASE)
                                if match:
                                    pdf_amount = float(match.group(1).replace(',', ''))
                            except Exception:
                                pass
                                
        if pdf_amount is not None:
            amount = pdf_amount
                        
        # Track summaries
        daily_summaries[address_folder][date_folder][eml_label] = {"amount": amount}
        monthly_summaries[address_folder][month_key] += amount

    print("Writing folder_address.json mapping...")
    with open(os.path.join(OUT_DIR, "folder_address.json"), "w") as f:
        json.dump(address_to_folder, f, indent=4)

    print("Writing summaries...")
    for address_folder, dates in daily_summaries.items():
        base_address_path = os.path.join(OUT_DIR, address_folder)
        
        # Write Monthly aggregate summary
        month_data = monthly_summaries[address_folder]
        sorted_months = sorted(month_data.keys())
        total_aggregate = sum(month_data.values())
        
        monthly_summary_dict = {
            "total_aggregate": round(total_aggregate, 2),
            "monthly_breakdown": {m: round(month_data[m], 2) for m in sorted_months}
        }
        with open(os.path.join(base_address_path, "summary.json"), "w") as f:
            json.dump(monthly_summary_dict, f, indent=4)
            
        md_lines = [f"# Spend Summary for {address_folder}", ""]
        md_lines.append(f"**Total Aggregate Spend**: ₹{total_aggregate:.2f}")
        md_lines.append("")
        md_lines.append("## Monthly Breakdown")
        for m in sorted_months:
            md_lines.append(f"- **{m}**: ₹{month_data[m]:.2f}")
            
        with open(os.path.join(base_address_path, "summary.md"), "w") as f:
            f.write("\n".join(md_lines) + "\n")
            
        # Write Daily summaries inside date folders
        for date_folder, emls in dates.items():
            date_dir = os.path.join(base_address_path, date_folder)
            
            total_amount = sum(item["amount"] for item in emls.values())
            
            summary_dict = {}
            for eml_key, data in emls.items():
                summary_dict[eml_key] = data
            summary_dict["total"] = {"amount": round(total_amount, 2)}
            
            with open(os.path.join(date_dir, "summary.json"), "w") as f:
                json.dump(summary_dict, f, indent=4)
            
    print("Done processing!")

if __name__ == "__main__":
    main()
