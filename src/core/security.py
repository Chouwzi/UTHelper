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
            
        # 2. Làm sạch các thuộc tính (loại bỏ các trình xử lý sự kiện 'on*' và 'style' nội dòng)
        for tag in soup.find_all(True):
            attrs = dict(tag.attrs)
            for attr in attrs:
                if attr.lower().startswith("on") or attr.lower() == "style":
                    del tag.attrs[attr]
        
        # 3. Xử lý Dark/Light mode: việc xóa thuộc tính 'style' ở trên là cách an toàn nhất để reset màu sắc.
        
        return str(soup)
