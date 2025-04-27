import fitz  # PyMuPDF
import google.generativeai as genai
import os
import re  # Import re module
import json # Import json module
from dotenv import load_dotenv

# Default logger function (prints to console)
def default_logger(message, level="INFO"):
    print(f"[{level}] {message}")

# --- Configuration ---
# Model name - check Google AI documentation for the latest free/flash model
GEMINI_MODEL_NAME = "gemini-2.5-flash-preview-04-17" 
# Safer Generation Config (optional, adjust as needed)
GENERATION_CONFIG = {
  "temperature": 0.2, # Lower temperature for more deterministic output
  "top_p": 1,
  "top_k": 1,
#   "max_output_tokens": 8192, # Adjust if needed, Flash has a large context window
}
SAFETY_SETTINGS = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
]

# --- Function Definitions ---

def configure_gemini(logger=default_logger):
    """Loads .env file and configures the Gemini API key."""
    # Load environment variables from .env file
    load_dotenv() 
    
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        logger("Error: GOOGLE_API_KEY not found. Ensure it is set in your '.env' file or as an environment variable.", "ERROR")
        return False
    try:
        genai.configure(api_key=api_key)
        logger("Gemini API configured successfully (using key from .env or environment).", "INFO")
        return True
    except Exception as e:
        logger(f"Error configuring Gemini API: {e}", "ERROR")
        return False

def get_chapter_ranges_from_toc(doc, logger=default_logger):
    # (Same function as before - finds chapters in TOC)
    toc = doc.get_toc(simple=False)
    potential_chapters = []
    # Basic pattern, might need refinement
    chapter_pattern = re.compile(r"^(chapter|part|section)\s+(\d+|[IVXLCDM]+)|^(introduction|conclusion|appendix|foreword|preface)", re.IGNORECASE)

    # Try level 1 first
    for item in toc:
        level, title, page_num = item[0], item[1], item[2]
        if level == 1 and page_num > 0:
            match = chapter_pattern.match(title.strip())
            # Also consider titles without explicit "Chapter" if they seem significant
            is_potential = match or (len(title.split()) < 5 and title.strip() == title.upper()) # Heuristic: short, uppercase titles
            if is_potential:
                 potential_chapters.append({"title": title.strip(), "page": page_num, "level": level})

    # If few/no level 1 chapters found, maybe broaden search (e.g., level 2?) - Optional
    if not potential_chapters:
        logger("Could not reliably identify chapters from Level 1 TOC entries.", "INFO")
        # Optional: Try other levels or patterns here if desired
        # Fallback: Use all level 1 entries
        for item in toc:
            level, title, page_num = item[0], item[1], item[2]
            if level == 1 and page_num > 0:
                 potential_chapters.append({"title": title.strip(), "page": page_num, "level": level})


    if not potential_chapters:
        return []

    # Sort by page number
    potential_chapters.sort(key=lambda x: x["page"])

    # Filter adjacent duplicates pointing to the same page (keep first)
    unique_chapters = []
    last_page = -1
    for chap in potential_chapters:
        if chap["page"] != last_page:
            unique_chapters.append(chap)
            last_page = chap["page"]
        else:
             logger(f"Note: Ignoring duplicate TOC entry for page {chap['page']}: '{chap['title']}'", "INFO")


    # Calculate ranges
    calculated_chapters = []
    total_pages = doc.page_count
    chapter_counter = 1

    for i, chap in enumerate(unique_chapters):
        start_page = chap["page"]
        end_page = total_pages # Default for the last chapter
        if i + 1 < len(unique_chapters):
            next_chap_start_page = unique_chapters[i+1]["page"]
            if next_chap_start_page > start_page:
                 end_page = next_chap_start_page - 1
            else:
                 # Should not happen often after duplicate filtering, but handle defensively
                 logger(f"Warning: Chapter '{chap['title']}' (pg {start_page}) followed by chapter '{unique_chapters[i+1]['title']}' (pg {next_chap_start_page}). Adjusting range.", "WARNING")
                 end_page = start_page # Make it a single page if starts are same/inverted

        if start_page <= end_page:
             calculated_chapters.append(
                 (chapter_counter, chap["title"], start_page, end_page)
             )
             chapter_counter += 1
        else:
             logger(f"Warning: Skipping chapter '{chap['title']}' due to invalid page range ({start_page}-{end_page}).", "WARNING")

    return calculated_chapters


def extract_text_with_page_markers(doc, logger=default_logger):
    """Extracts text from PDF, adding page markers."""
    full_text = ""
    logger("Extracting text for AI analysis (this might take a moment)...", "INFO")
    for i, page in enumerate(doc):
        page_num = i + 1
        text = page.get_text("text", sort=True) # Sort=True helps with reading order
        full_text += f"\n--- PAGE {page_num} ---\n{text}\n"
        if page_num % 50 == 0: # Progress indicator
            logger(f"  ... extracted page {page_num}/{doc.page_count}", "INFO")
    logger("Text extraction complete.", "INFO")
    return full_text


def get_chapter_ranges_from_ai(doc, logger=default_logger):
    """Attempts to identify chapter ranges using Gemini AI."""
    if not configure_gemini(logger=logger):
        return None # API not configured

    total_pages = doc.page_count
    extracted_text = extract_text_with_page_markers(doc, logger=logger)

    if not extracted_text:
        logger("Error: Could not extract text from the PDF.", "ERROR")
        return None

    prompt = f"""
Analyze the following text extracted from a PDF document with {total_pages} pages.

Your task is to identify the main chapters (like Introduction, Chapter 1, Chapter 2, Part I, Part II, Appendix, etc.) and determine their start and end page numbers (1-based).

Provide the output STRICTLY in JSON format. The JSON should be a list of objects, where each object represents a chapter and has the following keys:
- "chapter_num": An integer representing the sequential order (starting from 1).
- "title": A concise title for the chapter/section.
- "start_page": The 1-based page number where the chapter begins.
- "end_page": The 1-based page number where the chapter ends. The end page of one chapter should ideally be the page before the start page of the next. The last chapter must end on page {total_pages}.

Ensure the page ranges are contiguous and cover the document reasonably well from page 1 to {total_pages}. Make sure start_page and end_page are valid integers within [1, {total_pages}] and start_page <= end_page.

--- START OF DOCUMENT TEXT ---
{extracted_text}
--- END OF DOCUMENT TEXT ---

Respond ONLY with the JSON list. Do not include markdown backticks (```json ... ```) or any other text before or after the JSON.
"""

    logger(f"Sending text ({len(extracted_text)} chars) to Gemini AI model '{GEMINI_MODEL_NAME}'...", "INFO")
    try:
        model = genai.GenerativeModel(model_name=GEMINI_MODEL_NAME,
                                      generation_config=GENERATION_CONFIG,
                                      safety_settings=SAFETY_SETTINGS)
        
        # Use streaming for potentially long responses (though Flash has large context)
        # response = model.generate_content(prompt, stream=True)
        # full_response_text = ""
        # for chunk in response:
        #     full_response_text += chunk.text
        
        # Non-streaming (simpler for this use case if text isn't huge)
        response = model.generate_content(prompt)
        
        if not response.parts:
             logger("Warning: AI response was empty.", "WARNING")
             # Check candidate.finish_reason for details if available
             if hasattr(response, 'candidates') and response.candidates:
                 logger(f"  Finish Reason: {response.candidates[0].finish_reason}", "INFO")
                 if response.candidates[0].finish_reason != 'STOP':
                     logger("  Safety filters might have blocked the response.", "WARNING")
                     return None # Indicates potential issue
             return None

        raw_json = response.text.strip()
        logger("AI response received. Attempting to parse JSON...", "INFO")
        # Clean potential markdown code blocks
        if raw_json.startswith("```json"):
            raw_json = raw_json[7:]
        if raw_json.endswith("```"):
            raw_json = raw_json[:-3]
        raw_json = raw_json.strip()
        
        ai_chapters_raw = json.loads(raw_json)

        # Validate and format the AI output
        validated_chapters = []
        if not isinstance(ai_chapters_raw, list):
             logger("Error: AI did not return a JSON list.", "ERROR")
             return None

        for item in ai_chapters_raw:
            if not isinstance(item, dict):
                logger(f"Warning: Skipping invalid item in AI response: {item}", "WARNING")
                continue
            try:
                chap_num = int(item.get("chapter_num", 0))
                title = str(item.get("title", "Untitled AI Chapter")).strip()
                start_page = int(item.get("start_page", 0))
                end_page = int(item.get("end_page", 0))

                if not title: title = f"Chapter {chap_num} (AI)" # Add default title if empty
                
                # Basic validation
                if chap_num > 0 and 1 <= start_page <= end_page <= total_pages:
                    validated_chapters.append((chap_num, title, start_page, end_page))
                else:
                    logger(f"Warning: Skipping invalid range from AI: Chap {chap_num}, Pages {start_page}-{end_page}, Title: '{title}'", "WARNING")

            except (ValueError, TypeError) as e:
                logger(f"Warning: Skipping item due to parsing error ({e}): {item}", "WARNING")

        if not validated_chapters:
            logger("Error: AI response parsed, but no valid chapter ranges found.", "ERROR")
            return None

        # Sort by chapter number just in case AI didn't
        validated_chapters.sort(key=lambda x: x[0])
        
        # Optional: Add a check for gaps/overlaps here if needed
        
        logger("AI analysis complete. Found potential chapters.", "INFO")
        return validated_chapters

    except json.JSONDecodeError as e:
        logger(f"Error: Failed to parse AI response as JSON: {e}", "ERROR")
        logger("--- AI Raw Response Start ---", "DEBUG")
        logger(raw_json, "DEBUG")
        logger("--- AI Raw Response End ---", "DEBUG")
        return None
    except Exception as e:
        # Catch other potential API errors (rate limits, connection issues, etc.)
        logger(f"Error during AI interaction: {e}", "ERROR")
        # Specific check for ResourceExhausted which can indicate quota issues
        if "Resource has been exhausted" in str(e):
             logger("  This might indicate you've hit the free tier limits.", "WARNING")
        return None

def parse_manual_ranges(range_string, total_pages, logger=default_logger):
    """Parses a string like '1:5-20, 2:21-45' into chapter ranges."""
    chapters = []
    parts = range_string.split(',')
    try:
        for part in parts:
            part = part.strip()
            if not part: continue
            chap_num_str, page_range_str = part.split(':', 1)
            start_page_str, end_page_str = page_range_str.split('-', 1)

            chap_num = int(chap_num_str)
            start_page = int(start_page_str)
            end_page = int(end_page_str)

            if start_page <= 0 or end_page < start_page or end_page > total_pages:
                raise ValueError(f"Invalid range for chapter {chap_num}: {start_page}-{end_page} (Total Pages: {total_pages})")

            chapters.append((chap_num, f"Chapter {chap_num} (Manual)", start_page, end_page))

        chapters.sort(key=lambda x: x[0]) # Sort by chapter number
        return chapters
    except Exception as e:
        logger(f"Error parsing manual ranges: {e}", "ERROR")
        logger("Expected format: 1:5-20, 2:21-45, ... (ChapterNum:StartPage-EndPage)", "INFO")
        return None

def sanitize_filename(title):
    """Sanitizes a string to be used as a filename."""
    safe_title = re.sub(r'[\\/*?:"<>|]', "", title)  # Remove invalid filename chars
    safe_title = re.sub(r'\s+', '_', safe_title)  # Replace spaces with underscores
    return safe_title

def extract_chapters_to_pdf(doc, chapters, output_dir, offset=0, logger=default_logger):
    """Extracts chapters to individual PDF files, applying an optional page offset.

    Args:
        doc (fitz.Document): The opened PDF document.
        chapters (list): A list of tuples, each containing (chapter_num, title, start_page, end_page).
        output_dir (str): The directory to save the extracted chapter PDFs.
        offset (int): The page number offset to subtract from the chapter page numbers.
                      (e.g., if book page 1 is PDF page 5, offset should be 4).

    Returns:
        bool: True if extraction was successful, False otherwise.
    """
    try:
        os.makedirs(output_dir, exist_ok=True)
        total_pages_in_doc = doc.page_count

        for i, (chap_num, title, start_page, end_page) in enumerate(chapters):
            # Apply the offset
            # PDF page numbers are 0-based, chapter pages are usually 1-based
            # Adjusted start page (0-based index)
            adj_start_page_zero_based = start_page - offset - 1
            # Adjusted end page (0-based index)
            adj_end_page_zero_based = end_page - offset - 1

            # Validate adjusted page numbers
            if adj_start_page_zero_based < 0:
                logger(f"Warning: Chapter '{title}' (Original: {start_page}-{end_page}) adjusted start page {adj_start_page_zero_based+1} is less than 1 after applying offset {offset}. Skipping.", "WARNING")
                continue
            if adj_end_page_zero_based >= total_pages_in_doc:
                logger(f"Warning: Chapter '{title}' (Original: {start_page}-{end_page}) adjusted end page {adj_end_page_zero_based+1} exceeds document total ({total_pages_in_doc}) after applying offset {offset}. Clamping to max page.", "WARNING")
                adj_end_page_zero_based = total_pages_in_doc - 1
            if adj_start_page_zero_based > adj_end_page_zero_based:
                 logger(f"Warning: Chapter '{title}' (Original: {start_page}-{end_page}) adjusted start page {adj_start_page_zero_based+1} is greater than adjusted end page {adj_end_page_zero_based+1} after applying offset {offset}. Skipping.", "WARNING")
                 continue

            # Sanitize title for filename
            safe_title = sanitize_filename(title)
            
            # Determine filename: Use title directly if it looks like a chapter, otherwise prefix
            # Use regex to check if title starts with common chapter/section indicators
            if re.match(r'^(Chapter|Part|Section|Appendix|Introduction|Conclusion|Foreword|Preface)_', safe_title, re.IGNORECASE):
                base_filename = f"{safe_title}.pdf"
            else:
                # Fallback: Prefix with sequential chapter number if title doesn't have standard prefix
                base_filename = f"Chapter_{chap_num:02d}_{safe_title}.pdf"
                
            output_filename = os.path.join(output_dir, base_filename)

            # Create a new PDF for the chapter
            chapter_doc = fitz.open()  # Create an empty PDF

            # Insert the adjusted page range
            logger(f"Extracting '{title}': Original pages {start_page}-{end_page}, Adjusted PDF pages {adj_start_page_zero_based} to {adj_end_page_zero_based}", "INFO")
            chapter_doc.insert_pdf(doc, from_page=adj_start_page_zero_based, to_page=adj_end_page_zero_based)

            # Save the chapter PDF with optimization
            chapter_doc.save(output_filename, garbage=4, deflate=True, clean=True)
            chapter_doc.close()

        logger(f"\nSuccessfully extracted {len(chapters)} chapters to '{output_dir}'", "INFO")
        return True
    except Exception as e:
        logger(f"Error during chapter extraction: {e}", "ERROR")
        return False


# --- Main Execution ---
if __name__ == "__main__":
    logger = default_logger
    pdf_path = input("Enter the path to the PDF file: ")

    if not os.path.isfile(pdf_path):
        logger(f"Error: File not found or is not a file at '{pdf_path}'", "ERROR")
        exit()

    try:
        doc = fitz.open(pdf_path)
        logger(f"Opened '{os.path.basename(pdf_path)}', {doc.page_count} pages.", "INFO")
    except Exception as e:
        logger(f"Error opening PDF file: {e}", "ERROR")
        exit()

    confirmed_chapters = None
    source_method = None # Track where the confirmed chapters came from

    # 1. Try TOC
    logger("\n--- Attempting Table of Contents (TOC) Detection ---", "INFO")
    toc_chapters = get_chapter_ranges_from_toc(doc, logger=logger)

    if toc_chapters:
        logger("Potential chapters found via TOC:", "INFO")
        for chap_num, title, start, end in toc_chapters:
            logger(f"  Chap {chap_num}: '{title}' (Pages {start}-{end})", "INFO")

        while confirmed_chapters is None and source_method is None:
            choice = input("\nChoose action: [A]ccept TOC ranges, [T]ry AI detection, [M]anual entry, [Q]uit? ").upper()
            if choice == 'A':
                confirmed_chapters = toc_chapters
                source_method = "TOC"
            elif choice == 'T':
                source_method = "AI_Attempt" # Flag to try AI next
                break # Exit inner loop to proceed to AI step
            elif choice == 'M':
                source_method = "Manual_Attempt" # Flag to try Manual next
                break
            elif choice == 'Q':
                source_method = "Quit"
                break
            else:
                logger("Invalid choice.", "INFO")
    else:
        logger("No chapters found automatically via TOC.", "INFO")
        while source_method is None:
            choice = input("Choose action: [T]ry AI detection, [M]anual entry, [Q]uit? ").upper()
            if choice == 'T':
                 source_method = "AI_Attempt"
                 break
            elif choice == 'M':
                 source_method = "Manual_Attempt"
                 break
            elif choice == 'Q':
                 source_method = "Quit"
                 break
            else:
                logger("Invalid choice.", "INFO")


    # 2. Try AI if chosen or TOC failed
    if source_method == "AI_Attempt":
        logger("\n--- Attempting AI Detection (using Gemini) ---", "INFO")
        ai_chapters = get_chapter_ranges_from_ai(doc, logger=logger)

        if ai_chapters:
            logger("\nPotential chapters suggested by AI:", "INFO")
            for chap_num, title, start, end in ai_chapters:
                 logger(f"  Chap {chap_num}: '{title}' (Pages {start}-{end})", "INFO")
            
            while confirmed_chapters is None and source_method == "AI_Attempt": # Ensure we don't overwrite previous choice
                choice = input("Choose action: [A]ccept AI ranges, [M]anual entry, [Q]uit? ").upper()
                if choice == 'A':
                    confirmed_chapters = ai_chapters
                    source_method = "AI"
                elif choice == 'M':
                    source_method = "Manual_Attempt" # Flag to try Manual next
                    break
                elif choice == 'Q':
                    source_method = "Quit"
                    break
                else:
                    logger("Invalid choice.", "INFO")
        else:
            logger("AI detection failed or yielded no valid chapters.", "INFO")
            if source_method != "Quit": # Don't ask again if user already chose Quit
                 choice = input("Choose action: [M]anual entry, [Q]uit? ").upper()
                 if choice == 'M':
                     source_method = "Manual_Attempt"
                 else:
                     source_method = "Quit"


    # 3. Manual Entry if chosen or fallbacks failed
    if source_method == "Manual_Attempt":
        logger("\n--- Manual Chapter Range Entry ---", "INFO")
        while confirmed_chapters is None and source_method != "Quit":
            range_str = input(f"Enter ranges as ChapNum:StartPage-EndPage, separated by commas (1-{doc.page_count}):\n")
            manual_chapters = parse_manual_ranges(range_str, doc.page_count, logger=logger)
            if manual_chapters:
                logger("\nYou entered:", "INFO")
                for chap_num, title, start, end in manual_chapters:
                    logger(f"  Chap {chap_num}: (Pages {start}-{end})", "INFO")
                
                confirm = input("Confirm these ranges? [Y]es, [N]o (re-enter), [Q]uit? ").upper()
                if confirm == 'Y':
                    confirmed_chapters = manual_chapters
                    source_method = "Manual"
                elif confirm == 'Q':
                     source_method = "Quit"
                     break # Exit loop
                # If 'N' or invalid, loop continues to ask for input again
            else:
                # parse_manual_ranges prints error, ask if user wants to try again
                retry = input("Invalid format. Try entering ranges again? [Y]es, [Q]uit? ").upper()
                if retry != 'Y':
                    source_method = "Quit"
                    break # Exit loop


    # 4. Extraction if ranges are confirmed
    if confirmed_chapters and source_method not in ["Quit", None, "AI_Attempt", "Manual_Attempt"]:
        logger(f"\nProceeding with chapter ranges obtained via: {source_method}", "INFO")
        output_base = os.path.splitext(os.path.basename(pdf_path))[0]
        output_folder = f"{output_base}_chapters_{source_method}"
        extract_chapters_to_pdf(doc, confirmed_chapters, output_folder, logger=logger)
    elif source_method == "Quit":
        logger("Operation cancelled by user.", "INFO")
    else:
        # This case might happen if all attempts failed without explicit Quit
        logger("No valid chapter ranges were confirmed. Cannot proceed with extraction.", "INFO")

    # 5. Cleanup
    doc.close()
    logger("\nScript finished.", "INFO")