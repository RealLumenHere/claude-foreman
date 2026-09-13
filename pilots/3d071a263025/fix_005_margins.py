from docx import Document
from docx.shared import Cm

path = "/home/lumen/foreman/pilots/3d071a263025/work/treatment/officeval_005/综合设计指导书.docx"
doc = Document(path)
for section in doc.sections:
    section.page_width = Cm(21.0)
    section.page_height = Cm(29.7)
    section.left_margin = Cm(3.0)
    section.right_margin = Cm(2.5)
    section.top_margin = Cm(3.0)
    section.bottom_margin = Cm(2.5)
doc.save(path)
print("saved")
