from django.http import HttpResponse


def qr_svg_response(data):
    """Render a small SVG QR response using the project's existing qrcode package."""
    try:
        import qrcode
        import qrcode.image.svg
    except ImportError:
        return HttpResponse(
            'qrcode package is required to render QR SVG.',
            status=503,
            content_type='text/plain; charset=utf-8',
        )
    image = qrcode.make(
        data,
        image_factory=qrcode.image.svg.SvgPathImage,
        border=2,
        box_size=10,
    )
    response = HttpResponse(content_type='image/svg+xml')
    image.save(response)
    return response
