import os
from pypdf import PdfReader, PdfWriter
from collections import defaultdict

OUT_DIR = "segregated_orders"

def main():
    # address -> month ("YYYY-MM") -> list of pdf paths
    monthly_pdfs = defaultdict(lambda: defaultdict(list))
    
    # Traverse directories to group PDFs
    for root, dirs, files in os.walk(OUT_DIR):
        # We are looking for something like OUT_DIR / address / date_folder
        # So split root to see the path depth
        parts = os.path.relpath(root, OUT_DIR).split(os.sep)
        
        # Format matching: addressX / YYYYMMDD
        if len(parts) == 2 and parts[1].isdigit() and len(parts[1]) == 8:
            address_folder = parts[0]
            date_folder = parts[1]
            
            # Extract year and month string YYYY-MM
            month_key = f"{date_folder[:4]}-{date_folder[4:6]}"
            
            # Find all PDFs in this date folder
            # Keep them sorted by email number if possible
            pdf_files = [f for f in files if f.endswith('.pdf')]
            pdf_files.sort() # lexicographical sort is usually fine for 1.eml, 2.eml
            
            for f in pdf_files:
                monthly_pdfs[address_folder][month_key].append(os.path.join(root, f))
                
    total_merged = 0
    # Merge them together
    for address_folder, months in monthly_pdfs.items():
        base_address_path = os.path.join(OUT_DIR, address_folder)
        
        for month_key, pdf_paths in months.items():
            if not pdf_paths:
                continue
                
            writer = PdfWriter()
            all_1_page = True
            
            for pdf_path in pdf_paths:
                reader = PdfReader(pdf_path)
                if len(reader.pages) != 1:
                    all_1_page = False
                # Append all pages (in case some are more than 1 page)
                for page in reader.pages:
                    writer.add_page(page)
            
            # Save the concatenated file at the address root
            out_filename = f"merged_{month_key}.pdf"
            out_filepath = os.path.join(base_address_path, out_filename)
            
            with open(out_filepath, "wb") as f_out:
                writer.write(f_out)
                
            print(f"Merged {len(pdf_paths)} PDFs into {out_filepath} (All 1-page: {all_1_page})")
            total_merged += 1
            
    print(f"Completed! Generated {total_merged} monthly merged PDFs.")

if __name__ == "__main__":
    main()
