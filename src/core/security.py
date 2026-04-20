from bs4 import BeautifulSoup

class HTMLSanitizer:
    """
    Làm sạch nội dung HTML từ Moodle để ngăn chặn XSS và đảm bảo hiển thị sạch sẽ.
    Cụ thể là loại bỏ các script, style và các thẻ có hại tiềm ẩn khác.
    """
    @staticmethod
    def sanitize(html: str) -> str:
        if not html:
            return ""
            
        soup = BeautifulSoup(html, "lxml")
        
        # 1. Loại bỏ các thẻ nằm trong danh sách đen
        blacklist = ["script", "style", "iframe", "object", "embed", "applet", "meta", "link"]
        for tag in soup.find_all(blacklist):
            tag.decompose()
            
        # 2. Làm sạch các thuộc tính
        for tag in soup.find_all(True):
            attrs = dict(tag.attrs)
            for attr, val in attrs.items():
                al = attr.lower()
                # Loại bỏ các event handlers và style inline
                if al.startswith("on") or al == "style":
                    try:
                        del tag.attrs[attr]
                    except Exception:
                        pass
                    continue
                # Loại bỏ các URI nguy hiểm trong href/src
                if al in ("href", "src", "data-src", "srcdoc"):
                    try:
                        v = val.strip() if isinstance(val, str) else ""
                        if v.lower().startswith("javascript:") or v.lower().startswith("data:"):
                            try:
                                del tag.attrs[attr]
                            except Exception:
                                pass
                    except Exception:
                        try:
                            del tag.attrs[attr]
                        except Exception:
                            pass
        
        # 3. Xử lý Dark/Light mode: việc xóa thuộc tính 'style' ở trên là cách an toàn nhất để reset màu sắc.
        
        return str(soup)
