from io import BytesIO
from reportlab.lib.pagesizes import landscape, letter
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor
from reportlab.graphics.barcode import qr
from reportlab.graphics.shapes import Drawing
from reportlab.graphics import renderPDF
import os

def generate_certificate_pdf(name, course, date, code, verify_url, signature_image_path='C:\\Users\\chvkr\\Downloads\\COI\\sign.png'):
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=landscape(letter))
    width, height = landscape(letter)

    name = str(name) if name else "Unknown"
    course = str(course) if course else "Unknown Course"
    date = str(date) if date else "Unknown Date"
    code = str(code) if code else "Unknown Code"
    verify_url = str(verify_url) if verify_url else "Unknown URL"

    bg_color = HexColor('#ffffff')
    primary_gradient_start = HexColor('#667eea')
    primary_gradient_end = HexColor('#764ba2')
    secondary_gradient_start = HexColor('#f093fb')
    secondary_gradient_end = HexColor('#f5576c')
    text_dark = HexColor('#1e293b')
    text_medium = HexColor('#475569')
    text_light = HexColor('#64748b')
    border_color = HexColor('#e2e8f0')

    c.setFillColor(bg_color)
    c.rect(0, 0, width, height, fill=1)

    margin = 40
    border_width = 4

    c.setStrokeColor(primary_gradient_start)
    c.setLineWidth(border_width)
    c.roundRect(margin, margin, width - 2 * margin, height - 2 * margin, 15, fill=0)

    inner_margin = margin + 20
    c.setStrokeColor(border_color)
    c.setLineWidth(1)
    c.roundRect(inner_margin, inner_margin, width - 2 * inner_margin, height - 2 * inner_margin, 10, fill=0)

    c.setFont('Helvetica-Bold', 48)
    c.setFillColor(primary_gradient_start)
    title_y = height - 100
    c.drawCentredString(width / 2, title_y, 'Certificate')

    c.setFont('Helvetica', 16)
    c.setFillColor(text_light)
    subtitle_y = title_y - 30
    c.drawCentredString(width / 2, subtitle_y, 'O F   A C H I E V E M E N T')

    line_y = subtitle_y - 20
    c.setStrokeColor(secondary_gradient_start)
    c.setLineWidth(3)
    c.line(width / 2 - 100, line_y, width / 2 + 100, line_y)

    c.setFont('Helvetica', 14)
    c.setFillColor(text_medium)
    certify_y = line_y - 40
    c.drawCentredString(width / 2, certify_y, 'This is to certify that')

    c.setFont('Helvetica-Bold', 36)
    c.setFillColor(text_dark)
    name_y = certify_y - 50
    c.drawCentredString(width / 2, name_y, name)

    name_width = c.stringWidth(name, 'Helvetica-Bold', 36)
    c.setStrokeColor(secondary_gradient_start)
    c.setLineWidth(2)
    c.line(width / 2 - name_width / 2, name_y - 10, width / 2 + name_width / 2, name_y - 10)

    c.setFont('Helvetica', 16)
    c.setFillColor(text_medium)
    achievement_y = name_y - 40
    c.drawCentredString(width / 2, achievement_y, 'has successfully completed the course and demonstrated')
    c.drawCentredString(width / 2, achievement_y - 20, 'exceptional knowledge in')

    c.setFont('Helvetica-Bold', 24)
    c.setFillColor(secondary_gradient_start)
    course_y = achievement_y - 60
    c.drawCentredString(width / 2, course_y, course)

    details_y = course_y - 80
    box_width = 160
    box_height = 60
    box_spacing = 40

    total_width = 3 * box_width + 2 * box_spacing
    start_x = (width - total_width) / 2

    box1_x = start_x
    c.setFillColor(HexColor('#f8fafc'))
    c.setStrokeColor(border_color)
    c.setLineWidth(1)
    c.roundRect(box1_x, details_y, box_width, box_height, 8, fill=1)

    c.setFont('Helvetica', 10)
    c.setFillColor(text_light)
    c.drawCentredString(box1_x + box_width / 2, details_y + box_height - 15, 'DATE ISSUED')

    c.setFont('Helvetica-Bold', 14)
    c.setFillColor(text_dark)
    display_date = date[:18] + "..." if len(date) > 18 else date
    c.drawCentredString(box1_x + box_width / 2, details_y + 20, display_date)

    box2_x = start_x + box_width + box_spacing
    c.setFillColor(HexColor('#f8fafc'))
    c.roundRect(box2_x, details_y, box_width, box_height, 8, fill=1)

    c.setFont('Helvetica', 10)
    c.setFillColor(text_light)
    c.drawCentredString(box2_x + box_width / 2, details_y + box_height - 15, 'CERTIFICATE ID')

    c.setFont('Helvetica-Bold', 14)
    c.setFillColor(text_dark)
    display_code = code[:15] + "..." if len(code) > 15 else code
    c.drawCentredString(box2_x + box_width / 2, details_y + 20, display_code)

    box3_x = start_x + 2 * (box_width + box_spacing)
    c.setFillColor(HexColor('#f8fafc'))
    c.roundRect(box3_x, details_y, box_width, box_height, 8, fill=1)

    c.setFont('Helvetica', 10)
    c.setFillColor(text_light)
    c.drawCentredString(box3_x + box_width / 2, details_y + box_height - 15, 'VERIFICATION')

    c.setFont('Helvetica-Bold', 12)
    c.setFillColor(primary_gradient_start)
    c.drawCentredString(box3_x + box_width / 2, details_y + 20, 'VERIFIED')

    c.setFont('Helvetica-Oblique', 10)
    c.setFillColor(text_light)
    verify_y = details_y - 30
    display_url = verify_url[:80] + "..." if len(verify_url) > 80 else verify_url
    c.drawCentredString(width / 2, verify_y, f'Verify authenticity at: {display_url}')

    qr_data = f"{verify_url}?code={code}&name={name.replace(' ', '%20')}"
    qr_code = qr.QrCodeWidget(qr_data)
    qr_code.barWidth = 80
    qr_code.barHeight = 80
    qr_code.qrVersion = 1

    qr_drawing = Drawing(80, 80)
    qr_drawing.add(qr_code)

    qr_x = width - 130
    qr_y = 40
    renderPDF.draw(qr_drawing, c, qr_x, qr_y)

    c.setFont('Helvetica', 8)
    c.setFillColor(text_light)
    c.drawCentredString(qr_x + 40, qr_y - 15, 'Scan to Verify')

    signature_y = 80
    signature_x = 80

    if signature_image_path and os.path.exists(signature_image_path):
        try:
            c.drawImage(signature_image_path, signature_x, signature_y + 10, width=120, height=40, preserveAspectRatio=True)
        except Exception as e:
            c.setFont('Helvetica-Oblique', 16)
            c.setFillColor(text_dark)
            c.drawString(signature_x, signature_y + 20, 'Digital Signature')
    else:
        c.setFont('Helvetica-Oblique', 16)
        c.setFillColor(text_dark)
        c.drawString(signature_x, signature_y + 20, 'Authorized Signature')

    c.setStrokeColor(text_light)
    c.setLineWidth(1)
    c.line(signature_x, signature_y, signature_x + 170, signature_y)

    c.setFont('Helvetica-Bold', 12)
    c.setFillColor(text_medium)
    c.drawString(signature_x, signature_y - 20, 'The Certification Authority')

    c.setFont('Helvetica', 10)
    c.setFillColor(text_light)
    c.drawString(signature_x, signature_y - 30, f'Certificate ID: {code}')

    c.setFont('Helvetica', 8)
    c.setFillColor(text_light)
    c.drawCentredString(width / 2, 30, 'This certificate is digitally signed and verified.')

    c.save()
    buffer.seek(0)
    return buffer

if __name__ == "__main__":
    pdf_buffer = generate_certificate_pdf(
        name="John Doe",
        course="Advanced Python Programming",
        date="July 4, 2025",
        code="CERT-2025-001234",
        verify_url="https://example.com/verify/CERT-2025-001234"
    )
    with open("beautiful_certificate.pdf", "wb") as f:
        f.write(pdf_buffer.read())
    print("Beautiful certificate generated successfully!")
