import customtkinter as ctk
from typing import List
from datetime import datetime
from models import Assignment, UrgencyLevel
from config import settings

# Initialize default theme
ctk.set_appearance_mode(settings.THEME)  # "system", "dark", or "light"
ctk.set_default_color_theme("blue")  # Themes: "blue" (standard), "green", "dark-blue"

class AssignmentCard(ctk.CTkFrame):
    def __init__(self, master, assignment: Assignment, **kwargs):
        super().__init__(master, **kwargs)
        self.assignment = assignment
        
        # Determine colors based on urgency
        border_clr = "gray"
        if assignment.urgency == UrgencyLevel.CRITICAL:
            border_clr = "#ff4d4d"  # Red
        elif assignment.urgency == UrgencyLevel.WARNING:
            border_clr = "#ffcc00"  # Yellow
        elif assignment.urgency == UrgencyLevel.SAFE:
            border_clr = "#33cc33"  # Green
            
        self.configure(border_width=2, border_color=border_clr, corner_radius=10)
        
        # Layout
        self.grid_columnconfigure(0, weight=1)
        
        title_font = ctk.CTkFont(family="Segoe UI", size=14, weight="bold")
        info_font = ctk.CTkFont(family="Segoe UI", size=12)

        # Title
        self.lbl_title = ctk.CTkLabel(self, text=assignment.title, font=title_font, anchor="w")
        self.lbl_title.grid(row=0, column=0, padx=10, pady=(10, 2), sticky="w")
        
        # Course Name
        self.lbl_course = ctk.CTkLabel(self, text=assignment.course_name, font=info_font, text_color="gray", anchor="w")
        self.lbl_course.grid(row=1, column=0, padx=10, pady=0, sticky="w")
        
        # Deadline
        dt_str = assignment.deadline.strftime("%d/%m/%Y %H:%M")
        hrs = int(assignment.hours_remaining)
        text_time = f"Deadline: {dt_str} ({hrs}h remaining)"
        
        self.lbl_time = ctk.CTkLabel(self, text=text_time, font=info_font, text_color=border_clr, anchor="w")
        self.lbl_time.grid(row=2, column=0, padx=10, pady=(5, 10), sticky="w")

        # Open button
        self.btn_open = ctk.CTkButton(self, text="Open in Moodle", width=120, height=28, 
                                     fg_color="transparent", border_width=1,
                                     text_color=("gray10", "gray90"), hover_color=("gray70", "gray30"),
                                     command=self.open_url)
        self.btn_open.grid(row=0, rowspan=3, column=1, padx=10, pady=10, sticky="e")

    def open_url(self):
        import webbrowser
        webbrowser.open(self.assignment.url)

class DashboardApp(ctk.CTk):
    def __init__(self, assignments: List[Assignment]):
        super().__init__()
        
        self.assignments = assignments
        
        self.title("UTH Elearning Alert")
        self.geometry("600x700")
        self.minsize(400, 500)
        
        # Header
        self.header_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.header_frame.pack(fill="x", padx=20, pady=20)
        
        self.lbl_header = ctk.CTkLabel(self.header_frame, text="Upcoming Deadlines", 
                                       font=ctk.CTkFont(family="Segoe UI", size=24, weight="bold"))
        self.lbl_header.pack(side="left")
        
        # Scrollable Frame for assignments
        self.scroll_frame = ctk.CTkScrollableFrame(self)
        self.scroll_frame.pack(fill="both", expand=True, padx=20, pady=(0, 20))
        
        self.populate_assignments()
        
    def populate_assignments(self):
        for widget in self.scroll_frame.winfo_children():
            widget.destroy()
            
        if not self.assignments:
            lbl_empty = ctk.CTkLabel(self.scroll_frame, text="No pending assignments! You are totally safe 🎉",
                                     font=ctk.CTkFont(size=16))
            lbl_empty.pack(pady=50)
            return
            
        for a in self.assignments:
            card = AssignmentCard(self.scroll_frame, assignment=a)
            card.pack(fill="x", pady=5, padx=5)

def show_dashboard(assignments: List[Assignment]):
    app = DashboardApp(assignments)
    app.mainloop()

# For Testing standalone UI
if __name__ == "__main__":
    from datetime import timedelta
    dummy_data = [
        Assignment(id="1", course_id="c1", course_name="Network Programming", title="Assignment 1: Sockets", 
                   deadline=datetime.now() + timedelta(hours=12), url="https://example.com"),
        Assignment(id="2", course_id="c2", course_name="Data Structures", title="Lab 2", 
                   deadline=datetime.now() + timedelta(days=2), url="https://example.com"),
        Assignment(id="3", course_id="c3", course_name="Operating Systems", title="Final Project", 
                   deadline=datetime.now() + timedelta(days=5), url="https://example.com"),
    ]
    show_dashboard(dummy_data)
