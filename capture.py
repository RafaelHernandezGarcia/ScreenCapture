"""
Screen Capture - Screen capture functionality using mss library
"""
import mss
import mss.tools
from PIL import Image
import io


def capture_all_screens() -> Image.Image:
    """
    Capture the entire virtual desktop (all monitors combined).
    Returns a PIL Image object.
    """
    with mss.mss() as sct:
        # Monitor 0 is the "all in one" virtual monitor
        monitor = sct.monitors[0]
        screenshot = sct.grab(monitor)
        
        # Convert to PIL Image
        img = Image.frombytes("RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")
        return img


def capture_region(x: int, y: int, width: int, height: int) -> Image.Image:
    """
    Capture a specific region of the screen.
    
    Args:
        x: Left coordinate
        y: Top coordinate
        width: Width of region
        height: Height of region
    
    Returns:
        PIL Image of the captured region
    """
    with mss.mss() as sct:
        monitor = {"top": y, "left": x, "width": width, "height": height}
        screenshot = sct.grab(monitor)
        img = Image.frombytes("RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")
        return img


def get_virtual_screen_geometry() -> tuple:
    """
    Get the geometry of the virtual screen (all monitors combined).
    
    Returns:
        Tuple of (left, top, width, height)
    """
    with mss.mss() as sct:
        monitor = sct.monitors[0]
        return (monitor["left"], monitor["top"], monitor["width"], monitor["height"])


if __name__ == "__main__":
    # Test capture
    img = capture_all_screens()
    img.save("test_capture.png")
    print(f"Captured screen: {img.size}")
