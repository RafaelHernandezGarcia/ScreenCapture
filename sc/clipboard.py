"""NSPasteboard image copy."""
import io
from AppKit import NSPasteboard, NSPasteboardTypePNG
from Foundation import NSData


def copy_image(pil_image) -> None:
    buf = io.BytesIO()
    pil_image.save(buf, format="PNG")
    data = NSData.dataWithBytes_length_(buf.getvalue(), len(buf.getvalue()))
    pb = NSPasteboard.generalPasteboard()
    pb.clearContents()
    pb.setData_forType_(data, NSPasteboardTypePNG)
