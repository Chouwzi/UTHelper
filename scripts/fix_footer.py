import sys, os, re
sys.path.append(os.path.abspath('src'))

path = r'E:\Projects\UTH-Elearning-Alert\src\gui\app_controller.py'
with open(path, 'r', encoding='utf-8') as f: text = f.read()

# Replace _update_footer
new_method = '''    def _update_footer(self):
        from datetime import datetime as _dt
        from core.time_utils import parse_datetime as _parse_datetime
        now = _dt.now()
        
        # Base filter like in _render_cards
        base = self._apply_settings_filter(self.all_data)
        
        def match_urgency(d, u_target):
            dl_str = d.get("deadline", "")
            dl = _parse_datetime(dl_str) if dl_str else None
            if u_target == "overdue": return dl and dl < now
            if u_target == "all": return True
            if dl and dl < now: return False
            return urgency_str(d.get("urgency")) == u_target

        def match_type(d, t_target):
            if t_target == "all": return True
            if t_target == "open": return d.get("is_open", False)
            allowed = _TYPE_FILTER_MAP.get(t_target, {t_target})
            return d.get("type") in allowed and not d.get("is_open", False)

        def match_course(d, c_target):
            if c_target == "all": return True
            return clean_course_name(d.get("course", "")) == c_target

        n_critical = n_warning = n_safe = n_overdue = 0
        type_counts = {}
        course_names = set()
        course_counts = {}

        # 1. Compute course names & counts (filtered by Urgency and Type)
        for d in base:
            if match_urgency(d, self.active_urgency) and match_type(d, self.active_type):
                c_name = clean_course_name(d.get("course", "")) or "Sự kiện chung"
                course_names.add(c_name)
                course_counts[c_name] = course_counts.get(c_name, 0) + 1

        # 2. Compute urgency counts (filtered by Course and Type)
        for d in base:
            if match_course(d, self.active_course) and match_type(d, self.active_type):
                dl_str = d.get("deadline", "")
                dl = _parse_datetime(dl_str) if dl_str else None
                if dl and dl < now: n_overdue += 1
                else:
                    u = urgency_str(d.get("urgency"))
                    if u == "critical": n_critical += 1
                    elif u == "warning": n_warning += 1
                    else: n_safe += 1

        # 3. Compute type counts (filtered by Course and Urgency)
        for d in base:
            if match_course(d, self.active_course) and match_urgency(d, self.active_urgency):
                # Is open
                if d.get("is_open", False):
                    type_counts["open"] = type_counts.get("open", 0) + 1
                else:
                    raw_t = d.get("type", "other")
                    # For reverse mapping, maybe just check all types
                    for tk, allowed in _TYPE_FILTER_MAP.items():
                        if raw_t in allowed:
                            type_counts[tk] = type_counts.get(tk, 0) + 1

        def _on_course_select(e, c_name):
            self.page.run_task(self._set_course, c_name)

        c_items = [ft.PopupMenuItem(
            content=ft.Row([ft.Text("Tất cả môn học", size=12, color=C.TEXT_SECONDARY, expand=True), ft.Icon(ft.Icons.CHECK, size=12, color=C.TEXT_PRIMARY, visible=(self.active_course == "all"))], spacing=6, tight=True),
            on_click=lambda e: _on_course_select(e, "all")
        )]
        for c in sorted(list(course_names)):
            is_active = (self.active_course == c)
            cnt = course_counts.get(c, 0)
            c_items.append(ft.PopupMenuItem(
                content=ft.Row([
                    ft.Text(c, size=12, color=C.TEXT_PRIMARY, expand=True),
                    ft.Text(f"· {cnt}", size=11, color=C.TEXT_SECONDARY),
                    ft.Icon(ft.Icons.CHECK, size=12, color=C.TEXT_PRIMARY, visible=is_active)
                ], spacing=6, tight=True),
                on_click=lambda e, name=c: _on_course_select(e, name)
            ))
        self.course_popup.items = c_items

        self.footer_critical.value = f"Cấp bách · {n_critical}" if n_critical else ""
        self.footer_warning.value  = f"Sắp tới · {n_warning}" if n_warning else ""
        self.footer_safe.value     = f"An toàn · {n_safe}" if n_safe else ""
        self.footer_overdue.value  = f"Quá hạn · {n_overdue}" if n_overdue else ""

        self._update_urgency_counts({"critical": n_critical, "warning": n_warning, "safe": n_safe, "overdue": n_overdue})
        self._update_type_counts(type_counts)'''

regex = re.compile(r'    def _update_footer\(self\):.*?(?=    async def _set_urgency)', re.DOTALL)
new_text = regex.sub(new_method + '\n\n', text)
with open(path, 'w', encoding='utf-8') as f: f.write(new_text)

print("Replaced _update_footer()")
