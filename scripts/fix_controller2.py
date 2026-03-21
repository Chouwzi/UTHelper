import sys, os, re
sys.path.append(os.path.abspath('src'))

path = r'E:\Projects\UTH-Elearning-Alert\src\gui\app_controller.py'
with open(path, 'r', encoding='utf-8') as f: text = f.read()

# Replace enriched unpacking to include 'course' in both _load_data_async and _prefetch_details_async

old_unpack1 = '''                    self.all_data[i] = {
                        **item,
                        "type": enriched.get("type", item.get("type", "other")),
                        "submission_status": enriched.get("submission_status", "unknown"),
                        "details": enriched.get("details", {}),
                    }'''

new_unpack1 = '''                    self.all_data[i] = {
                        **item,
                        "type": enriched.get("type", item.get("type", "other")),
                        "course": enriched.get("course", item.get("course", "")),
                        "submission_status": enriched.get("submission_status", "unknown"),
                        "details": enriched.get("details", {}),
                    }'''

old_unpack2 = '''                    self.all_data[i] = {
                        **item,
                        "type": enriched.get("type", item["type"]),
                        "submission_status": enriched.get("submission_status", "unknown"),
                        "details": enriched.get("details", {}),
                    }'''

new_unpack2 = '''                    self.all_data[i] = {
                        **item,
                        "type": enriched.get("type", item.get("type", "other")),
                        "course": enriched.get("course", item.get("course", "")),
                        "submission_status": enriched.get("submission_status", "unknown"),
                        "details": enriched.get("details", {}),
                    }'''

text = text.replace(old_unpack1, new_unpack1)
text = text.replace(old_unpack2, new_unpack2)

# Now inject self._update_footer() into the end of _prefetch_details_async
end_prefetch = '''            self._card_cache.clear()
            self._render_cards()
            self.status_text.value = f"Cập nhật lúc {datetime.now().strftime('%H:%M')} • {len(self.all_data)} hoạt động  ✓ sẵn sàng"'''

end_prefetch_new = '''            self._card_cache.clear()
            self._update_footer()
            self._render_cards()
            self.status_text.value = f"Cập nhật lúc {datetime.now().strftime('%H:%M')} • {len(self.all_data)} hoạt động  ✓ sẵn sàng"'''

text = text.replace(end_prefetch, end_prefetch_new)

with open(path, 'w', encoding='utf-8') as f: f.write(text)
print("Updated successfully")
