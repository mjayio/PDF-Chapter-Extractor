#!/usr/bin/env python3

import os
import sys
import threading
import re
import json
import time
from datetime import datetime
from dotenv import load_dotenv

# Try to import Tkinter with fallbacks for different systems
try:
    import tkinter as tk
    from tkinter import filedialog, ttk, messagebox, scrolledtext
except ImportError:
    print("Tkinter not available. You might need to install it:")
    print("- For macOS: brew install python-tk@3.13 or xcode-select --install")
    print("- For Linux: sudo apt-get install python3-tk")
    print("- For Windows: Tkinter should be included in standard installations")
    sys.exit(1)

try:
    import fitz  # PyMuPDF
except ImportError:
    print("PyMuPDF not found. Installing...")
    os.system("pip install pymupdf")
    import fitz

try:
    import google.generativeai as genai
except ImportError:
    print("Google Generative AI package not found. Installing...")
    os.system("pip install google-generativeai")
    import google.generativeai as genai

# Import core functions from the original script
try:
    from smart_splitter import (
        GEMINI_MODEL_NAME, 
        GENERATION_CONFIG, 
        SAFETY_SETTINGS,
        configure_gemini, 
        get_chapter_ranges_from_toc, 
        get_chapter_ranges_from_ai, 
        extract_chapters_to_pdf,
        parse_manual_ranges
    )
except ImportError:
    print("Error importing from smart_splitter.py - make sure it's in the same directory")
    sys.exit(1)

class PDFExtractorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("PDF Chapter Extractor")
        
        # Enhanced window management
        self.root.geometry("900x700")  # Larger default size
        self.root.minsize(700, 500)    # Minimum window size
        
        # Enable full window management
        self.setup_window_management()
        
        self.pdf_path = None
        self.doc = None
        self.chapters = None
        self.source_method = None
        self.page_offset_var = tk.IntVar(value=0)  # Variable for page offset
        self.tree_item_data = {} # To map tree item IDs to chapter data
        
        # Add logging capability
        self.log_entries = []
        self.log_visible = False
        self.paned_window = None  # Initialize paned window
        
        self.create_ui()
        
        # Initialize with a welcome log entry
        self.add_log_entry("Application started", "INFO")
    
    def setup_window_management(self):
        """Configure window management capabilities"""
        # Allow window to be resizable (should be default on macOS, but explicitly setting)
        self.root.resizable(True, True)
        
        # Set window icon if available (more relevant for Windows/Linux)
        try:
            # On macOS, the app icon would be set through the application bundle
            if sys.platform != 'darwin':
                self.root.iconbitmap('icon.ico')  # Add an icon file to your project
        except:
            pass
            
        # Add maximize button explicitly for macOS
        if sys.platform == 'darwin':
            try:
                # Enable the zoom (maximize) button on macOS
                self.root.attributes('-zoomed', '1')
                # Explicitly set window style to include all buttons
                self.root.createcommand('::tk::mac::RealizeWindowStyle', lambda: None)
                self.root.createcommand('::tk::mac::ShowWindowsMenu', lambda: None)
            except:
                pass
        
        # Add window state tracking for better resize handling
        self.root.bind("<Configure>", self.on_window_configure)
        
        # Add key bindings for common window operations
        self.root.bind("<Escape>", lambda e: self.toggle_fullscreen())
        
        # For macOS, add Command+Q to quit
        if sys.platform == 'darwin':
            self.root.bind("<Command-q>", lambda e: self.root.quit())
            # Command+W to close window
            self.root.bind("<Command-w>", lambda e: self.root.withdraw())
    
    def on_window_configure(self, event):
        """Handle window resize events"""
        # Only process events from the main window, not widgets
        if event.widget == self.root:
            # This function could adjust layouts based on window size
            # For now it's a placeholder for future enhancements
            pass
            
    def toggle_fullscreen(self):
        """Toggle fullscreen mode"""
        # Get current state
        state = False
        if sys.platform == 'darwin':
            # macOS has a different way of handling fullscreen
            try:
                state = self.root.attributes('-fullscreen')
                self.root.attributes('-fullscreen', not state)
            except:
                # Fallback if attributes method doesn't work
                self.root.attributes('-zoomed', '1')
        else:
            # Windows/Linux method
            state = self.root.attributes('-fullscreen')
            self.root.attributes('-fullscreen', not state)
            
    def create_ui(self):
        # Main frame
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Top section - File Selection
        file_frame = ttk.LabelFrame(main_frame, text="PDF Selection", padding="10")
        file_frame.pack(fill=tk.X, pady=5)
        file_frame.columnconfigure(1, weight=1)  # Make entry expand
        
        self.file_path_var = tk.StringVar()
        ttk.Label(file_frame, text="PDF File:").grid(row=0, column=0, sticky=tk.W, padx=5, pady=5)
        ttk.Entry(file_frame, textvariable=self.file_path_var, width=50, state="readonly").grid(row=0, column=1, padx=5, pady=5, sticky=tk.EW)
        ttk.Button(file_frame, text="Browse...", command=self.browse_pdf).grid(row=0, column=2, padx=5, pady=5)
        
        # Add Page Offset input
        ttk.Label(file_frame, text="Page Number Offset:").grid(row=1, column=0, sticky=tk.W, padx=5, pady=5)
        offset_spinbox = ttk.Spinbox(file_frame, from_=-100, to=100, textvariable=self.page_offset_var, width=5)
        offset_spinbox.grid(row=1, column=1, sticky=tk.W, padx=5, pady=5)
        ttk.Label(file_frame, text="(Pages to subtract from book's page numbers)").grid(row=1, column=2, sticky=tk.W, padx=5, pady=5)
        
        # Middle section - Detection Methods
        method_frame = ttk.LabelFrame(main_frame, text="Chapter Detection", padding="10")
        method_frame.pack(fill=tk.X, pady=5)
        
        # Detection method buttons
        btn_frame = ttk.Frame(method_frame)
        btn_frame.pack(fill=tk.X, pady=5)
        
        ttk.Button(btn_frame, text="Detect via TOC", command=self.detect_toc).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Detect via AI", command=self.detect_ai).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="Manual Entry", command=self.manual_entry).pack(side=tk.LEFT, padx=5)
        
        # Progress indicator
        self.progress_var = tk.DoubleVar()
        self.progress = ttk.Progressbar(method_frame, variable=self.progress_var, maximum=100)
        self.progress.pack(fill=tk.X, pady=5)
        
        # Create a PanedWindow to hold chapters and log
        self.paned_window = ttk.PanedWindow(main_frame, orient=tk.VERTICAL)
        self.paned_window.pack(fill=tk.BOTH, expand=True, pady=5)

        # Chapters display section (add to PanedWindow)
        chapters_frame = ttk.LabelFrame(self.paned_window, text="Detected Chapters", padding="10")
        self.paned_window.add(chapters_frame, weight=3) # Add to paned window with a weight
        
        # Create Treeview for chapters
        columns = ("select", "chapter", "title", "start_page", "end_page") # Add 'select' column
        self.chapter_tree = ttk.Treeview(chapters_frame, columns=columns, show="headings")
        
        # Define column headings
        self.chapter_tree.heading("select", text="Select")
        self.chapter_tree.heading("chapter", text="Chapter")
        self.chapter_tree.heading("title", text="Title")
        self.chapter_tree.heading("start_page", text="Start Page")
        self.chapter_tree.heading("end_page", text="End Page")
        
        # Configure column widths and alignment (adjust as needed)
        self.chapter_tree.column("select", width=50, anchor=tk.CENTER, stretch=tk.NO)
        self.chapter_tree.column("chapter", width=70, anchor=tk.W)
        self.chapter_tree.column("title", width=300, anchor=tk.W)
        self.chapter_tree.column("start_page", width=80, anchor=tk.E)
        self.chapter_tree.column("end_page", width=80, anchor=tk.E)

        # Add scrollbar
        scrollbar = ttk.Scrollbar(chapters_frame, orient=tk.VERTICAL, command=self.chapter_tree.yview)
        self.chapter_tree.configure(yscrollcommand=scrollbar.set)
        
        # Remove old selection tag config
        # self.chapter_tree.tag_configure('selected', background='lightblue') 
        
        # Bind click event to toggle checkbox
        self.chapter_tree.bind('<Button-1>', self.toggle_checkbox) # Changed binding
        
        # Pack tree and scrollbar
        self.chapter_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Remove Select All / Deselect All buttons
        # select_btn_frame = ttk.Frame(chapters_frame)
        # select_btn_frame.pack(fill=tk.X, pady=(5,0))
        # ttk.Button(select_btn_frame, text="Select All", command=self.select_all).pack(side=tk.LEFT, padx=5)
        # ttk.Button(select_btn_frame, text="Deselect All", command=self.deselect_all).pack(side=tk.LEFT, padx=5)

        # Bottom section - Action buttons and log toggle
        action_frame = ttk.Frame(main_frame)
        
        self.status_var = tk.StringVar(value="Please select a PDF file")
        ttk.Label(action_frame, textvariable=self.status_var).pack(side=tk.LEFT, padx=5)
        
        # Log toggle button
        self.log_btn = ttk.Button(action_frame, text="Show Log", command=self.toggle_log_panel)
        self.log_btn.pack(side=tk.RIGHT, padx=5)
        
        self.extract_btn = ttk.Button(action_frame, text="Extract Chapters", command=self.extract_chapters, state=tk.DISABLED)
        self.extract_btn.pack(side=tk.RIGHT, padx=5)
        
        # Collapsible log panel (hidden by default)
        self.log_frame = ttk.LabelFrame(main_frame, text="Operation Log", padding="10")
        
        # Create log display as a Text widget with scrollbar
        log_container = ttk.Frame(self.log_frame)
        log_container.pack(fill=tk.BOTH, expand=True)

        # Use dark theme colors for the log display
        log_bg_color = "#2E2E2E"  # Dark grey background (adjust if needed)
        log_fg_color = "#CCCCCC"  # Light grey text (adjust if needed)

        self.log_display = scrolledtext.ScrolledText(log_container, wrap=tk.WORD, height=8,
                                                    background=log_bg_color,
                                                    foreground=log_fg_color,
                                                    font=("Consolas", 10),
                                                    insertbackground=log_fg_color) # Cursor color
        self.log_display.pack(fill=tk.BOTH, expand=True, pady=5)
        self.log_display.config(state=tk.DISABLED)  # Read-only

        # Configure tags for coloring on dark background
        self.log_display.tag_configure("error", foreground="#FF6B6B") # Lighter Red
        self.log_display.tag_configure("warning", foreground="#FFA500") # Orange (often visible on dark)
        self.log_display.tag_configure("success", foreground="#90EE90") # Light Green

        # Log control buttons
        log_btn_frame = ttk.Frame(self.log_frame)
        log_btn_frame.pack(fill=tk.X)
        
        ttk.Button(log_btn_frame, text="Clear Log", 
                  command=self.clear_log).pack(side=tk.LEFT, padx=5)
        ttk.Button(log_btn_frame, text="Save Log...", 
                  command=self.save_log).pack(side=tk.LEFT, padx=5)
        
        # Manual entry dialog components
        self.manual_dialog = None
        
        # Pack the action frame LAST to keep it at the bottom
        action_frame.pack(fill=tk.X, pady=5, side=tk.BOTTOM) # Added side=tk.BOTTOM
    
    def toggle_log_panel(self):
        """Toggle the log panel visibility"""
        self.log_visible = not self.log_visible
        
        if self.log_visible:
            self.paned_window.add(self.log_frame, weight=1) # Add log frame to paned window
            self.log_btn.config(text="Hide Log")
            self.update_log_display()  # Refresh log content
        else:
            self.paned_window.forget(self.log_frame) # Remove log frame from paned window
            self.log_btn.config(text="Show Log")
    
    def add_log_entry(self, message, level="INFO"):
        """Add a new entry to the log with timestamp"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        entry = f"[{timestamp}] [{level}] {message}"
        self.log_entries.append(entry)
        
        # If log is visible, update the display
        if self.log_visible:
            self.update_log_display()
    
    def update_log_display(self):
        """Update the log text widget with all entries"""
        # Enable editing to update content
        self.log_display.config(state=tk.NORMAL)
        
        # Clear and add all entries
        self.log_display.delete(1.0, tk.END)
        for entry in self.log_entries:
            # Color coding based on log level
            if "[ERROR]" in entry:
                self.log_display.insert(tk.END, entry + "\n", "error")
            elif "[WARNING]" in entry:
                self.log_display.insert(tk.END, entry + "\n", "warning")
            elif "[SUCCESS]" in entry:
                self.log_display.insert(tk.END, entry + "\n", "success")
            else:
                self.log_display.insert(tk.END, entry + "\n")
        
        # Configure tags for coloring
        self.log_display.tag_configure("error", foreground="red")
        self.log_display.tag_configure("warning", foreground="orange")
        self.log_display.tag_configure("success", foreground="green")
        
        # Scroll to the end
        self.log_display.see(tk.END)
        
        # Set back to read-only
        self.log_display.config(state=tk.DISABLED)
    
    def clear_log(self):
        """Clear the log entries and display"""
        self.log_entries = []
        self.add_log_entry("Log cleared", "INFO")
    
    def save_log(self):
        """Save the log to a file"""
        file_path = filedialog.asksaveasfilename(
            defaultextension=".log",
            filetypes=[("Log Files", "*.log"), ("Text Files", "*.txt"), ("All Files", "*.*")],
            title="Save Log As"
        )
        
        if file_path:
            try:
                with open(file_path, 'w') as f:
                    for entry in self.log_entries:
                        f.write(entry + "\n")
                self.add_log_entry(f"Log saved to {file_path}", "SUCCESS")
            except Exception as e:
                self.add_log_entry(f"Failed to save log: {e}", "ERROR")
                messagebox.showerror("Save Error", f"Failed to save log: {e}")
    
    def browse_pdf(self):
        """Opens file dialog to select a PDF"""
        file_path = filedialog.askopenfilename(
            title="Select PDF file",
            filetypes=[("PDF Files", "*.pdf"), ("All Files", "*.*")]
        )
        
        if file_path:
            self.pdf_path = file_path
            self.file_path_var.set(file_path)
            self.status_var.set("PDF loaded. Select a detection method.")
            self.add_log_entry(f"Selected PDF: {file_path}")
            
            # Open the PDF document
            try:
                if self.doc:
                    self.doc.close()
                    
                self.doc = fitz.open(file_path)
                page_count = self.doc.page_count
                self.status_var.set(f"Loaded '{os.path.basename(file_path)}', {page_count} pages")
                self.add_log_entry(f"Opened PDF with {page_count} pages")
                
                # Clear previous results
                self.chapters = None
                self.source_method = None
                self.tree_item_data.clear() # Clear stored chapter data
                for item in self.chapter_tree.get_children():
                    self.chapter_tree.delete(item)
                
                self.extract_btn.config(state=tk.DISABLED)
            except Exception as e:
                messagebox.showerror("Error", f"Failed to open PDF: {e}")
                self.status_var.set("Error opening PDF file")
                self.add_log_entry(f"Error opening PDF: {e}", "ERROR")
    
    def detect_toc(self):
        """Detects chapters using the Table of Contents"""
        if not self.check_doc():
            return
            
        self.status_var.set("Detecting chapters via TOC...")
        self.progress_var.set(10)
        self.add_log_entry("Starting TOC-based chapter detection")
        
        # Run in a thread to avoid blocking the UI
        def process():
            try:
                start_time = time.time()
                toc_chapters = get_chapter_ranges_from_toc(self.doc, logger=self.add_log_entry)
                elapsed = time.time() - start_time
                
                # Update UI in the main thread
                self.root.after(0, lambda: self.add_log_entry(f"TOC detection completed in {elapsed:.2f} seconds"))
                self.root.after(0, lambda: self.display_chapters(toc_chapters, "TOC"))
            except Exception as e:
                self.root.after(0, lambda: self.add_log_entry(f"TOC detection error: {e}", "ERROR"))
                self.root.after(0, lambda: self.handle_error(f"TOC detection error: {e}"))
        
        threading.Thread(target=process).start()
    
    def detect_ai(self):
        """Detects chapters using AI"""
        if not self.check_doc():
            return
            
        if not configure_gemini(logger=self.add_log_entry):
            messagebox.showwarning("API Configuration", 
                                  "Gemini API not configured. Please check your .env file for GOOGLE_API_KEY.")
            self.status_var.set("AI detection unavailable - API not configured")
            return
            
        self.status_var.set("Detecting chapters via AI (this may take a while)...")
        self.progress_var.set(10)
        self.add_log_entry("Starting AI-based chapter detection")
        self.add_log_entry("This process may take several minutes depending on document size", "INFO")
        
        # Run in a thread to avoid blocking the UI
        def process():
            try:
                self.root.after(0, lambda: self.progress.config(mode="indeterminate"))
                self.root.after(0, lambda: self.progress.start())
                
                start_time = time.time()
                ai_chapters = get_chapter_ranges_from_ai(self.doc, logger=self.add_log_entry)
                elapsed = time.time() - start_time
                
                self.root.after(0, lambda: self.progress.stop())
                self.root.after(0, lambda: self.progress.config(mode="determinate"))
                self.root.after(0, lambda: self.add_log_entry(f"AI detection completed in {elapsed:.2f} seconds"))
                self.root.after(0, lambda: self.display_chapters(ai_chapters, "AI"))
            except Exception as e:
                # Use lambda default argument to capture exception correctly
                self.root.after(0, lambda err=e: self.handle_error(f"AI detection error: {err}"))
                self.root.after(0, lambda: self.progress_var.set(0))
                self.root.after(0, lambda: self.status_var.set("AI detection failed. Check log."))
                self.root.after(0, lambda: self.extract_btn.config(state=tk.DISABLED))
        
        threading.Thread(target=process).start()
    
    def manual_entry(self):
        """Opens dialog for manual chapter entry"""
        if not self.check_doc():
            return
            
        self.manual_dialog = tk.Toplevel(self.root)
        self.manual_dialog.title("Manual Chapter Entry")
        self.manual_dialog.geometry("600x400")
        self.manual_dialog.transient(self.root)
        self.manual_dialog.grab_set()
        
        ttk.Label(self.manual_dialog, 
                 text=f"Enter chapter ranges in format: ChapterNum:StartPage-EndPage\n"
                      f"Multiple chapters should be separated by commas.\n"
                      f"Example: 1:1-10, 2:11-20, 3:21-30\n"
                      f"Document has {self.doc.page_count} pages total.").pack(pady=10)
        
        text_frame = ttk.Frame(self.manual_dialog)
        text_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        self.manual_text = scrolledtext.ScrolledText(text_frame, wrap=tk.WORD, height=10)
        self.manual_text.pack(fill=tk.BOTH, expand=True)
        
        btn_frame = ttk.Frame(self.manual_dialog)
        btn_frame.pack(fill=tk.X, padx=10, pady=10)
        
        ttk.Button(btn_frame, text="Cancel", 
                  command=self.manual_dialog.destroy).pack(side=tk.RIGHT, padx=5)
        ttk.Button(btn_frame, text="Apply", 
                  command=self.process_manual_entry).pack(side=tk.RIGHT, padx=5)
    
    def process_manual_entry(self):
        """Processes the manually entered chapter ranges"""
        if not self.manual_dialog:
            return

        range_str = self.manual_text.get("1.0", tk.END).strip()
        manual_chapters = parse_manual_ranges(range_str, self.doc.page_count, logger=self.add_log_entry)

        if manual_chapters:
            self.manual_dialog.destroy()
            self.display_chapters(manual_chapters, "Manual")
        else:
            messagebox.showerror("Invalid Format",
                               "Could not parse the chapter ranges. Please check the format and the log panel.")
    
    def display_chapters(self, chapters, source):
        """Displays the detected chapters in the UI"""
        for item in self.chapter_tree.get_children():
            self.chapter_tree.delete(item)
            
        self.tree_item_data.clear() # Clear previous mapping
        
        if not chapters:
            self.progress_var.set(0)
            self.status_var.set(f"No chapters detected via {source}. Try another method.")
            self.add_log_entry(f"No chapters detected via {source} method", "WARNING")
            return
            
        for chap_num, title, start, end in chapters:
            # Insert item with checkbox checked by default
            item_id = self.chapter_tree.insert("", tk.END, values=('☑', chap_num, title, start, end), tags=('checked',))
            # Store the actual chapter data associated with the item ID
            self.tree_item_data[item_id] = (chap_num, title, start, end)
            self.add_log_entry(f"Found chapter: {title} (Pages {start}-{end})")
        
        self.chapters = chapters # Keep the original full list if needed elsewhere
        
        self.progress_var.set(100)
        self.status_var.set(f"Found {len(chapters)} chapters via {source}. Ready to extract.")
        self.add_log_entry(f"Detection complete - Found {len(chapters)} chapters using {source} method", "SUCCESS")
        self.extract_btn.config(state=tk.NORMAL)
    
    def toggle_checkbox(self, event):
        """Toggle the checkbox state when the 'Select' column is clicked."""
        region = self.chapter_tree.identify("region", event.x, event.y)
        if region != "cell":
            return # Clicked outside a cell

        column_id = self.chapter_tree.identify_column(event.x)
        item_id = self.chapter_tree.identify_row(event.y)
        
        # Check if the click was on the first column ('#1' which corresponds to "select")
        if column_id == '#1': 
            tags = self.chapter_tree.item(item_id, 'tags')
            if 'checked' in tags:
                # Uncheck
                new_tags = tuple(t for t in tags if t != 'checked') + ('unchecked',)
                self.chapter_tree.item(item_id, tags=new_tags, values=('☐',) + self.chapter_tree.item(item_id, 'values')[1:])
            else:
                # Check (or re-check if somehow untagged)
                new_tags = tuple(t for t in tags if t != 'unchecked') + ('checked',)
                self.chapter_tree.item(item_id, tags=new_tags, values=('☑',) + self.chapter_tree.item(item_id, 'values')[1:])
    
    def extract_chapters(self):
        """Extracts chapters based on the checked items in the treeview"""
        # Get selected chapters from the treeview
        if not self.check_doc(): # Removed check for self.chapters as it's less relevant now
            return

        # Get the list of checked chapters from the Treeview
        selected_chapters_data = []
        for item_id in self.chapter_tree.get_children():
            # Check the 'checked' tag
            if 'checked' in self.chapter_tree.item(item_id, 'tags'):
                if item_id in self.tree_item_data:
                    selected_chapters_data.append(self.tree_item_data[item_id])
        
        if not selected_chapters_data:
            messagebox.showwarning("No Selection", "No chapters are checked for extraction.")
            self.add_log_entry("Extraction skipped - no chapters checked", "WARNING")
            return

        try:
            offset = self.page_offset_var.get()
        except tk.TclError:
            messagebox.showerror("Invalid Offset", "Page number offset must be an integer.")
            self.add_log_entry("Invalid page offset value entered", "ERROR")
            return

        output_base = os.path.splitext(os.path.basename(self.pdf_path))[0]
        default_output = f"{output_base}_chapters_{self.source_method}"
        
        output_dir = filedialog.askdirectory(
            title="Select Output Directory",
            initialdir=os.path.dirname(self.pdf_path)
        )
        
        if not output_dir:
            self.add_log_entry("Chapter extraction cancelled by user")
            return
            
        output_path = os.path.join(output_dir, default_output)
        
        if os.path.exists(output_path):
            confirm = messagebox.askyesno(
                "Directory Exists",
                f"Output directory '{output_path}' already exists. Continue anyway?"
            )
            if not confirm:
                self.add_log_entry("Chapter extraction cancelled - directory already exists")
                return
        
        self.status_var.set("Extracting chapters...")
        self.progress_var.set(0)
        if offset != 0:
            self.add_log_entry(f"Starting chapter extraction to: {output_path} with page offset: {offset}")
        else:
            self.add_log_entry(f"Starting chapter extraction to: {output_path}")
        
        def process():
            try:
                total_chapters = len(selected_chapters_data) # Use selected count
                increment = 100.0 / total_chapters if total_chapters > 0 else 0
                
                self.root.after(0, lambda: self.add_log_entry(f"Applying page offset of {offset} during extraction", "INFO"))
                
                # Iterate over selected chapters for progress updates and logging
                for i, chapter in enumerate(selected_chapters_data):
                    progress_val = (i+0.5)*increment
                    chap_num = chapter[0]
                    self.root.after(0, lambda val=progress_val: self.progress_var.set(val))
                
                for i, chapter in enumerate(self.chapters):
                    progress_val = (i+0.5)*increment
                    chap_num = chapter[0]
                    self.root.after(0, lambda val=progress_val: self.progress_var.set(val))
                    self.root.after(0, lambda chap=chap_num: 
                                  self.status_var.set(f"Extracting chapter {chap}..."))
                    self.root.after(0, lambda chap=chapter, i=i+1, total=total_chapters: 
                                   self.add_log_entry(f"Extracting chapter {i}/{total}: {chap[1]}"))
                
                    chap_num, title, start_page, end_page = chapter
                    log_msg = f"Extracting chapter {i+1}/{total_chapters}: {title} (Pages {start_page}-{end_page})"
                    if offset != 0:
                        adj_start = start_page - offset
                        adj_end = end_page - offset
                        log_msg += f" -> Adjusted: {adj_start}-{adj_end}"
                    self.root.after(0, lambda msg=log_msg: self.add_log_entry(msg))
                
                start_time = time.time()
                # Pass only the selected chapters to the extraction function
                success = extract_chapters_to_pdf(self.doc, selected_chapters_data, output_path, offset=offset, logger=self.add_log_entry)
                elapsed = time.time() - start_time
                
                if success:
                    self.root.after(0, lambda: self.progress_var.set(100))
                    self.root.after(0, lambda: self.status_var.set(
                        f"Extraction complete. {len(self.chapters)} chapter(s) saved to '{output_path}'"))
                    self.root.after(0, lambda: self.add_log_entry(
                        f"Extraction completed in {elapsed:.2f} seconds - {len(self.chapters)} chapters extracted", 
                        "SUCCESS"))
                    self.root.after(0, lambda: messagebox.showinfo(
                        "Success", f"Successfully extracted {len(self.chapters)} chapters to:\n{output_path}"))
                else:
                    self.root.after(0, lambda: self.handle_error("Failed to extract chapters (check log for details)"))
            except Exception as e:
                self.root.after(0, lambda: self.add_log_entry(f"Extraction error: {e}", "ERROR"))
                self.root.after(0, lambda: self.handle_error(f"Extraction error: {e}"))
        
        threading.Thread(target=process).start()
    
    def check_doc(self):
        """Checks if a document is loaded"""
        if not self.doc:
            messagebox.showerror("No Document", "Please open a PDF document first")
            self.add_log_entry("Operation failed - No document loaded", "ERROR")
            return False
        return True
    
    def handle_error(self, error_msg):
        """Handles errors and updates UI"""
        messagebox.showerror("Error", error_msg)
        self.progress_var.set(0)
        self.status_var.set("Error: " + error_msg)
        self.add_log_entry(error_msg, "ERROR")

def main():
    if sys.platform == 'darwin':
        try:
            from tkmacosx import Button
        except ImportError:
            pass
    
    root = tk.Tk()
    
    if sys.platform == 'darwin':
        try:
            root.wm_attributes('-titlepath', os.path.abspath(__file__))
            root.createcommand('::tk::mac::Quit', root.destroy)
            root.createcommand('::tk::mac::OnHide', lambda: None)
            root.createcommand('::tk::mac::OnShow', lambda: None)
            root.createcommand('::tk::mac::ShowPreferences', lambda: None)
            root.tk.call('::tk::unsupported::MacWindowStyle', 'style', root._w, 'document', 'closeBox resizable zoomBox collapseBox')
        except Exception as e:
            print(f"Warning: Could not set macOS window attributes: {e}")
    
    app = PDFExtractorApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()