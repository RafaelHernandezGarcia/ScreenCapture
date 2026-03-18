"""
Drawing Tools - Tool implementations for annotations
"""
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional
from PyQt6.QtCore import Qt, QPoint, QRect, QPointF
from PyQt6.QtGui import QPainter, QPen, QColor, QFont, QPolygonF
import math

SYSTEM_FONT = ".AppleSystemUIFont" if sys.platform == "darwin" else "Segoe UI"


@dataclass
class DrawingAction:
    """Represents a single drawing action for undo/redo"""
    tool_type: str
    color: QColor
    points: List[QPoint] = field(default_factory=list)
    rect: Optional[QRect] = None
    text: str = ""
    font_size: int = 16
    line_width: int = 3


class BaseTool(ABC):
    """Abstract base class for all drawing tools"""
    
    def __init__(self, color: QColor = QColor(255, 0, 0), line_width: int = 3):
        self.color = color
        self.line_width = line_width
        self.start_point: Optional[QPoint] = None
        self.current_point: Optional[QPoint] = None
        self.is_drawing = False
    
    @abstractmethod
    def get_name(self) -> str:
        pass
    
    @abstractmethod
    def on_mouse_press(self, point: QPoint):
        pass
    
    @abstractmethod
    def on_mouse_move(self, point: QPoint):
        pass
    
    @abstractmethod
    def on_mouse_release(self, point: QPoint) -> Optional[DrawingAction]:
        pass
    
    @abstractmethod
    def draw_preview(self, painter: QPainter):
        """Draw the current tool preview while dragging"""
        pass


class ArrowTool(BaseTool):
    """Draw arrows"""
    
    def get_name(self) -> str:
        return "Arrow"
    
    def on_mouse_press(self, point: QPoint):
        self.start_point = point
        self.current_point = point
        self.is_drawing = True
    
    def on_mouse_move(self, point: QPoint):
        if self.is_drawing:
            self.current_point = point
    
    def on_mouse_release(self, point: QPoint) -> Optional[DrawingAction]:
        if self.start_point and self.is_drawing:
            self.is_drawing = False
            action = DrawingAction(
                tool_type="arrow",
                color=QColor(self.color),
                points=[QPoint(self.start_point), QPoint(point)],
                line_width=self.line_width
            )
            self.start_point = None
            self.current_point = None
            return action
        return None
    
    def draw_preview(self, painter: QPainter):
        if self.start_point and self.current_point and self.is_drawing:
            draw_arrow(painter, self.start_point, self.current_point, self.color, self.line_width)


class RectangleTool(BaseTool):
    """Draw rectangles"""
    
    def get_name(self) -> str:
        return "Rectangle"
    
    def on_mouse_press(self, point: QPoint):
        self.start_point = point
        self.current_point = point
        self.is_drawing = True
    
    def on_mouse_move(self, point: QPoint):
        if self.is_drawing:
            self.current_point = point
    
    def on_mouse_release(self, point: QPoint) -> Optional[DrawingAction]:
        if self.start_point and self.is_drawing:
            self.is_drawing = False
            rect = QRect(self.start_point, point).normalized()
            action = DrawingAction(
                tool_type="rectangle",
                color=QColor(self.color),
                rect=rect,
                line_width=self.line_width
            )
            self.start_point = None
            self.current_point = None
            return action
        return None
    
    def draw_preview(self, painter: QPainter):
        if self.start_point and self.current_point and self.is_drawing:
            pen = QPen(self.color, self.line_width)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            rect = QRect(self.start_point, self.current_point).normalized()
            painter.drawRect(rect)


class CircleTool(BaseTool):
    """Draw circles/ellipses"""
    
    def get_name(self) -> str:
        return "Circle"
    
    def on_mouse_press(self, point: QPoint):
        self.start_point = point
        self.current_point = point
        self.is_drawing = True
    
    def on_mouse_move(self, point: QPoint):
        if self.is_drawing:
            self.current_point = point
    
    def on_mouse_release(self, point: QPoint) -> Optional[DrawingAction]:
        if self.start_point and self.is_drawing:
            self.is_drawing = False
            rect = QRect(self.start_point, point).normalized()
            action = DrawingAction(
                tool_type="circle",
                color=QColor(self.color),
                rect=rect,
                line_width=self.line_width
            )
            self.start_point = None
            self.current_point = None
            return action
        return None
    
    def draw_preview(self, painter: QPainter):
        if self.start_point and self.current_point and self.is_drawing:
            pen = QPen(self.color, self.line_width)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            rect = QRect(self.start_point, self.current_point).normalized()
            painter.drawEllipse(rect)


class LineTool(BaseTool):
    """Draw straight lines"""
    
    def get_name(self) -> str:
        return "Line"
    
    def on_mouse_press(self, point: QPoint):
        self.start_point = point
        self.current_point = point
        self.is_drawing = True
    
    def on_mouse_move(self, point: QPoint):
        if self.is_drawing:
            self.current_point = point
    
    def on_mouse_release(self, point: QPoint) -> Optional[DrawingAction]:
        if self.start_point and self.is_drawing:
            self.is_drawing = False
            action = DrawingAction(
                tool_type="line",
                color=QColor(self.color),
                points=[QPoint(self.start_point), QPoint(point)],
                line_width=self.line_width
            )
            self.start_point = None
            self.current_point = None
            return action
        return None
    
    def draw_preview(self, painter: QPainter):
        if self.start_point and self.current_point and self.is_drawing:
            pen = QPen(self.color, self.line_width)
            painter.setPen(pen)
            painter.drawLine(self.start_point, self.current_point)


class PenTool(BaseTool):
    """Freehand drawing"""
    
    def __init__(self, color: QColor = QColor(255, 0, 0), line_width: int = 3):
        super().__init__(color, line_width)
        self.points: List[QPoint] = []
    
    def get_name(self) -> str:
        return "Pen"
    
    def on_mouse_press(self, point: QPoint):
        self.points = [point]
        self.is_drawing = True
    
    def on_mouse_move(self, point: QPoint):
        if self.is_drawing:
            self.points.append(point)
    
    def on_mouse_release(self, point: QPoint) -> Optional[DrawingAction]:
        if self.is_drawing and self.points:
            self.is_drawing = False
            action = DrawingAction(
                tool_type="pen",
                color=QColor(self.color),
                points=[QPoint(p) for p in self.points],
                line_width=self.line_width
            )
            self.points = []
            return action
        return None
    
    def draw_preview(self, painter: QPainter):
        if self.points and self.is_drawing:
            pen = QPen(self.color, self.line_width, Qt.PenStyle.SolidLine, 
                      Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            # Pen tool typically doesn't use brush, but good to clear it
            painter.setBrush(Qt.BrushStyle.NoBrush)
            for i in range(1, len(self.points)):
                painter.drawLine(self.points[i-1], self.points[i])


class HighlighterTool(BaseTool):
    """Semi-transparent highlighter"""
    
    def __init__(self, color: QColor = QColor(255, 255, 0), line_width: int = 20):
        super().__init__(color, line_width)
        self.points: List[QPoint] = []
    
    def get_name(self) -> str:
        return "Highlighter"
    
    def on_mouse_press(self, point: QPoint):
        self.points = [point]
        self.is_drawing = True
    
    def on_mouse_move(self, point: QPoint):
        if self.is_drawing:
            self.points.append(point)
    
    def on_mouse_release(self, point: QPoint) -> Optional[DrawingAction]:
        if self.is_drawing and self.points:
            self.is_drawing = False
            highlight_color = QColor(self.color)
            highlight_color.setAlpha(100)
            action = DrawingAction(
                tool_type="highlighter",
                color=highlight_color,
                points=[QPoint(p) for p in self.points],
                line_width=self.line_width
            )
            self.points = []
            return action
        return None
    
    def draw_preview(self, painter: QPainter):
        if self.points and self.is_drawing:
            highlight_color = QColor(self.color)
            highlight_color.setAlpha(100)
            pen = QPen(highlight_color, self.line_width, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            for i in range(1, len(self.points)):
                painter.drawLine(self.points[i-1], self.points[i])


class TextTool(BaseTool):
    """Add text annotations"""
    
    def __init__(self, color: QColor = QColor(255, 0, 0), font_size: int = 16):
        super().__init__(color, 1)
        self.font_size = font_size
        self.click_point: Optional[QPoint] = None
    
    def get_name(self) -> str:
        return "Text"
    
    def on_mouse_press(self, point: QPoint):
        self.click_point = point
    
    def on_mouse_move(self, point: QPoint):
        pass
    
    def on_mouse_release(self, point: QPoint) -> Optional[DrawingAction]:
        return None
    
    def get_click_point(self) -> Optional[QPoint]:
        point = self.click_point
        self.click_point = None
        return point
    
    def draw_preview(self, painter: QPainter):
        pass


# Utility functions

def draw_arrow(painter: QPainter, start: QPoint, end: QPoint, color: QColor, line_width: int = 3):
    """Draw an arrow from start to end"""
    painter.save() # SAVE STATE
    try:
        pen = QPen(color, line_width)
        painter.setPen(pen)
        painter.setBrush(color) # This fills the arrow head
        
        # Draw main line
        painter.drawLine(start, end)
        
        # Calculate arrow head
        arrow_size = 15
        dx = end.x() - start.x()
        dy = end.y() - start.y()
        
        # Don't draw head if line is too short
        if (dx*dx + dy*dy) < 25:
            return

        angle = math.atan2(dy, dx)
        
        p1 = QPointF(
            end.x() - arrow_size * math.cos(angle - math.pi/6),
            end.y() - arrow_size * math.sin(angle - math.pi/6)
        )
        p2 = QPointF(
            end.x() - arrow_size * math.cos(angle + math.pi/6),
            end.y() - arrow_size * math.sin(angle + math.pi/6)
        )
        
        arrow_head = QPolygonF([QPointF(end), p1, p2])
        painter.drawPolygon(arrow_head)
    finally:
        painter.restore() # RESTORE STATE (Crucial fix for the fill bug)


def draw_action(painter: QPainter, action: DrawingAction):
    """Draw a saved action"""
    line_width = getattr(action, 'line_width', 3)
    
    if action.tool_type == "arrow":
        if len(action.points) >= 2:
            draw_arrow(painter, action.points[0], action.points[1], action.color, line_width)
    
    elif action.tool_type == "rectangle":
        if action.rect:
            pen = QPen(action.color, line_width)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(action.rect)
    
    elif action.tool_type == "circle":
        if action.rect:
            pen = QPen(action.color, line_width)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(action.rect)
    
    elif action.tool_type == "line":
        if len(action.points) >= 2:
            pen = QPen(action.color, line_width)
            painter.setPen(pen)
            painter.drawLine(action.points[0], action.points[1])
    
    elif action.tool_type == "pen":
        if action.points:
            pen = QPen(action.color, line_width, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            for i in range(1, len(action.points)):
                painter.drawLine(action.points[i-1], action.points[i])
    
    elif action.tool_type == "highlighter":
        if action.points:
            # Save state for highlighter transparency
            painter.save()
            pen = QPen(action.color, line_width, Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            
            # Using specific composition mode for highlighter effect if desired, 
            # but standard alpha blending is usually enough
            for i in range(1, len(action.points)):
                painter.drawLine(action.points[i-1], action.points[i])
            painter.restore()
    
    elif action.tool_type == "text":
        if action.points and action.text:
            font = QFont(SYSTEM_FONT, action.font_size or 18, QFont.Weight.Bold)
            painter.setFont(font)
            painter.setPen(action.color)
            painter.drawText(action.points[0], action.text)