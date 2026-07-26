import qrcode
from io import BytesIO
from PIL import Image

def generate_qr_code(text):
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=10,
        border=4,
    )
    qr.add_data(text)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")

    bio = BytesIO()
    img.save(bio, 'PNG')
    bio.seek(0)
    return bio
