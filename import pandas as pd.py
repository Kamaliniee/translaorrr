from reportlab.pdfgen import canvas

pdf = canvas.Canvas("large_test.pdf")

text = "This is a test PDF for translation portal load testing. " * 50

for page in range(50000):
    pdf.drawString(50, 750, text)
    pdf.drawString(50, 700, text)
    pdf.drawString(50, 650, text)
    pdf.showPage()

pdf.save()

print("PDF created: large_test.pdf")