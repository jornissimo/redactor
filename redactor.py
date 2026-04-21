from markdown2 import markdown
from markdown_pdf import MarkdownPdf, Section
import numpy as np
import os
import pyqtgraph as pg
from PyQt6.QtWidgets import (QApplication, QMainWindow, QTextEdit, QHBoxLayout, 
                             QWidget, QFileDialog, QLabel, QVBoxLayout, QMenu, QMessageBox, QTextBrowser)
from PyQt6.QtGui import QKeySequence, QAction
from PyQt6.QtCore import Qt, QUrl
import pymorphy3
import pyperclip
import re
import shutil
import sys
import uuid

try:
    with open("styles.qss", "r", encoding="utf-8") as f:
        DARK_STYLE = f.read()
except FileNotFoundError:
    DARK_STYLE = ""
    print("Файл styles.qss не найден")

try:
    with open("preview.css", "r", encoding="utf-8") as f:
        HTML_CSS_CONTENT = f.read()
except FileNotFoundError:
    HTML_CSS_CONTENT = ""
    print("Файл prewiew.css не найден")

class LexicalEditor(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Precision Semantic")
        self.resize(1300, 950)
        self.setStyleSheet(DARK_STYLE)
        
        self.tension_history = [0]
        self.init_ui()
        self.create_menu()
        self.morph = pymorphy3.MorphAnalyzer()
        self.text_modified = False

        self.paste_action = QAction(self)
        self.paste_action.setShortcut(QKeySequence("Ctrl+V"))
        self.paste_action.triggered.connect(self.force_paste)
        self.addAction(self.paste_action)
        self.input_area.installEventFilter(self)
        # For drag-and-drop
        self.input_area.setAcceptDrops(True)
        # Redirecting drop events to our methods
        self.input_area.dragEnterEvent = self.dragEnterEvent
        self.input_area.dropEvent = self.dropEvent
        self.input_area.textChanged.connect(self.mark_as_modified)

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
    
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(25, 25, 25, 25) 
        main_layout.setSpacing(20)

        # Work area
        editor_layout = QHBoxLayout()
        editor_layout.setSpacing(25)

        self.input_area = QTextEdit()
        self.input_area.setPlaceholderText("// input...")
        self.input_area.textChanged.connect(self.on_text_changed)

        self.preview_area = QTextBrowser()
        self.preview_area.setReadOnly(True)
        # For local files
        self.preview_area.document().setBaseUrl(QUrl.fromLocalFile(os.getcwd() + "/"))
        # For specific path
        self.preview_area.setSearchPaths([os.path.expanduser("~"), os.getcwd()])
        
        self.html_css = f"<style>{HTML_CSS_CONTENT}</style>"

        editor_layout.addWidget(self.input_area)
        editor_layout.addWidget(self.preview_area)
        main_layout.addLayout(editor_layout, stretch=4)

        # Tension monitor
        graph_container = QWidget()
        graph_layout = QVBoxLayout(graph_container)
        graph_layout.setContentsMargins(0,0,0,0)
        
        self.graph_label = QLabel("LEXICAL TENSION MONITOR")
        graph_layout.addWidget(self.graph_label)

        self.plot_widget = pg.PlotWidget()
        self.plot_widget.setBackground('#050505')
        self.plot_widget.setMouseEnabled(x=False, y=False)
        self.plot_widget.hideAxis('left')
        self.plot_widget.getAxis('bottom').setPen('#222')
        
        self.curve = self.plot_widget.plot(pen=pg.mkPen(color='#fff', width=1.5))
        graph_layout.addWidget(self.plot_widget)
        
        main_layout.addWidget(graph_container, stretch=1)
        
        self.stats_label = QLabel("READY")
        main_layout.addWidget(self.stats_label)

    # For WSL Linux copy-paste
    def keyPressEvent(self, event):
        if event.modifiers() == Qt.KeyboardModifier.ControlModifier and event.key() == Qt.Key.Key_V:
            self.force_paste()
        else:
            # Process all other keys as usual
            super().keyPressEvent(event)

    def force_paste(self):
        import pyperclip
        try:
            text = pyperclip.paste()
            if text:
                self.input_area.insertPlainText(text)
                
                # Breaking the insert into parts for the graph
                # To prevent the graph from being a straight line, I will run 
                # the calculation paragraph by paragraph.
                paragraphs = text.split('\n')
                cumulative_text = self.input_area.toPlainText()[:-(len(text))] # текст ДО вставки
                
                for p in paragraphs:
                    if p.strip():
                        cumulative_text += p + '\n'
                        self.calculate_tension(cumulative_text)
                
                # Final preview update
                self.on_text_changed()
        except Exception as e:
            print(f"Ошибка вставки: {e}")

    def show_custom_menu(self, position):
        menu = self.input_area.createStandardContextMenu()
        # For WSL Linux paste
        for action in menu.actions():
            if "Paste" in action.text() or "Вставить" in action.text():
                action.triggered.disconnect() # Отключаем стандарт
                action.triggered.connect(self.force_paste) # Подключаем наш с обновлением графика
        menu.exec(self.input_area.mapToGlobal(position))


    def create_menu(self):
        menu_bar = self.menuBar() # Переменная доступна внутри этого метода
        
        # Menu file
        file_menu = menu_bar.addMenu("file")
        
        # Open file (Ctrl+O)
        open_action = QAction("open", self)
        open_action.setShortcut("Ctrl+O")
        open_action.triggered.connect(self.open_file)
        file_menu.addAction(open_action)

        # Save MD
        save_md_action = QAction("save (.MD)", self)
        save_md_action.setShortcut("Ctrl+S")
        save_md_action.triggered.connect(self.save_md)
        file_menu.addAction(save_md_action)
        
        # Export PDF
        export_pdf_action = QAction("export to (.PDF)", self)
        export_pdf_action.setShortcut("Ctrl+P")
        export_pdf_action.triggered.connect(self.export_pdf)
        file_menu.addAction(export_pdf_action)

        # Format's menu
        format_menu = menu_bar.addMenu("format")
        tags = [
            ("Заголовок 1", "# "),
            ("Заголовок 2", "## "),
            ("Заголовок 3", "### "),
            ("Заголовок 4", "#### "),
            ("---", "sep"), 
            ("Жирный", "**текст**"),
            ("Курсив", "_текст_"),
            ("Код", "```\nкод\n```"),
            ("Цитата", "> "),
            ("Список", "- ")
        ]

        for label, syntax in tags:
            if syntax != "sep":
                action = QAction(label, self)
                action.triggered.connect(lambda ch, s=syntax: self.insert_text(s))
                format_menu.addAction(action)
            else:
                format_menu.addSeparator()

        format_menu.addSeparator()
        format_menu.addAction("Вставить изображение").triggered.connect(self.insert_image)

    def open_file(self):
        # Open file dialog
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Открыть файл", "", 
            "Markdown Files (*.md);;Text Files (*.txt);;All Files (*)"
        )
        
        if file_path:
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                # Load text to dialog
                self.input_area.setPlainText(content)
                
                # Reset the tension
                self.tension_history = []
                self.curve.setData([])
                
                # Updating the preview and status
                self.on_text_changed()
                
                # Display the name of the open file in the status bar
                filename = os.path.basename(file_path)
                self.stats_label.setText(f"OPENED: {filename}")
                self.setWindowTitle(f"PF // {filename}")
                
            except Exception as e:
                QMessageBox.critical(self, "Ошибка", f"Не удалось открыть файл:\n{str(e)}")
        self.text_modified = False


    def get_versioned_path(self, path):
        base_path, ext = os.path.splitext(path)
        base_path = re.sub(r'_v\d+$', '', base_path)
        counter = 1
        final_path = f"{base_path}_v{counter}{ext}"
        while os.path.exists(final_path):
            counter += 1
            final_path = f"{base_path}_v{counter}{ext}"
        return final_path

    def save_md(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save Markdown", "", "Markdown (*.md)")
        if path:
            final_path = self.get_versioned_path(path)
            try:
                with open(final_path, 'w', encoding='utf-8') as f:
                    f.write(self.input_area.toPlainText())
                self.stats_label.setText(f"SAVED: {os.path.basename(final_path)}")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Could not save MD: {str(e)}")
        self.text_modified = False

    def export_pdf(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export PDF", "", "PDF Files (*.pdf)")
        if path:
            final_path = self.get_versioned_path(path)
            try:
                pdf = MarkdownPdf()
                pdf.add_section(Section(self.input_area.toPlainText()))
                pdf.save(final_path)
                QMessageBox.information(self, "Success", f"PDF Exported:\n{os.path.basename(final_path)}")
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Could not export PDF: {str(e)}")

    def insert_text(self, syntax):
        cursor = self.input_area.textCursor()
        cursor.insertText(syntax)
        self.input_area.setFocus()
        self.on_text_changed() 

    def on_text_changed(self):
        text = self.input_area.toPlainText()
        html_body = markdown(text, extras=["images", "tables", "fenced-code-blocks"])
        
        # Базовый URL как папку со скриптом, чтобы "media/..." работало
        # Specify the base URL as the folder with the script so that "media/..." works
        base_url = QUrl.fromLocalFile(os.path.dirname(os.path.abspath(__file__)) + "/")
        self.preview_area.setHtml(self.html_css + html_body, base_url)
        
        self.calculate_tension(text)

    # If file close -> save as
    def mark_as_modified(self):
        self.text_modified = True

    def closeEvent(self, event):
        if not self.text_modified:
            event.accept()
            return

        # If text_modified = True than create custom window
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("Внимание")
        msg_box.setText("В документе есть несохраненные изменения.")
        
        # Button style
        save_button = msg_box.addButton("Сохранить как", QMessageBox.ButtonRole.ActionRole)
        discard_button = msg_box.addButton("Все равно закрыть", QMessageBox.ButtonRole.DestructiveRole)
        cancel_button = msg_box.addButton("Отмена", QMessageBox.ButtonRole.RejectRole)

        msg_box.exec()

        if msg_box.clickedButton() == save_button:
            # Save function
            self.save_md()
            # Cancel in dialog, if after saving the file is still marked as modified
            if self.text_modified:
                event.ignore()
            else:
                event.accept()
        elif msg_box.clickedButton() == discard_button:
            event.accept() # Close without save
        else:
            event.ignore() # Back to editor


    def calculate_tension(self, text):
        # Вычисляет TTR по всему тексту и локальную напряженность для динамики графика
        # Calculates TTR for the entire text and local tension for graph dynamics
        import re
        # Normalization words
        words_only = re.findall(r'[а-яёa-z]+', text.lower())
        
        if not words_only:
            self.tension_history = []
            self.curve.setData([])
            self.stats_label.setText("TTR (LEM): 0.00 | WORDS: 0")
            return

        # Our Type Token Ratio
        lemmatized_all = [self.morph.parse(w)[0].normal_form for w in words_only]
        total_count = len(lemmatized_all)
        unique_count = len(set(lemmatized_all))
        ttr = unique_count / total_count

        # Local tension for "live" graph
        # Делить текст на предложения / divide the text into sentences
        sentences = [s.strip() for s in re.split(r'[.!?]+', text) if len(s.strip()) > 2]
        
        if sentences:
            # Анализ только последних 3 предложений / analysis of only the last 3 sentences
            recent_segment = " ".join(sentences[-3:])
            recent_words = re.findall(r'[а-яёa-z]+', recent_segment.lower())
            
            if recent_words:
                recent_lemmas = [self.morph.parse(w)[0].normal_form for w in recent_words]
                local_ttr = len(set(recent_lemmas)) / len(recent_lemmas)
                local_avg_len = len(recent_words) / len(sentences[-3:])
                
                # Формула напряженности: смесь локального разнообразия и длины фраз
                # Tension Formula: A Mixture of Local Diversity and Phrase Length
                # Больше веса длине (0.6) для корректной реакции графика на сложные фразы
                tension = (local_ttr * 0.4) + (min(local_avg_len, 30) / 30 * 0.6)
            else:
                tension = 0
        else:
            tension = 0
        
        # Update graph history
        self.tension_history.append(tension)
        if len(self.tension_history) > 100:
            self.tension_history.pop(0)
            
        self.curve.setData(self.tension_history)
        
        # Updating the status (overall TTR)
        self.stats_label.setText(f"TTR (LEM): {ttr:.2f} | WORDS: {total_count}")
        self.curve.setData(self.tension_history)
        self.plot_widget.enableAutoRange(axis='y', enable=True)
        self.plot_widget.viewport().update() 
        QApplication.processEvents()
        self.plot_widget.update()

        self.curve.setData(self.tension_history)
        
        self.plot_widget.autoRange()
        self.plot_widget.viewport().update()

    def on_text_changed(self):
        cursor = self.input_area.textCursor()
        pos = cursor.position()
        text = self.input_area.toPlainText()
        
        # Block signals to prevent autocorrect from looping the function
        self.input_area.blockSignals(True)

        if pos >= 3 and text[pos-3:pos] == " - ":
            cursor.movePosition(cursor.MoveOperation.Left, cursor.MoveMode.KeepAnchor, 3)
            cursor.insertText(" — ")

        elif pos >= 2 and text[pos-2:pos] == "--":
            cursor.movePosition(cursor.MoveOperation.Left, cursor.MoveMode.KeepAnchor, 2)
            cursor.insertText("–")

        elif pos >= 1 and text[pos-1:pos] == '"':
            before = text[pos-2:pos-1] if pos > 1 else " "
            cursor.movePosition(cursor.MoveOperation.Left, cursor.MoveMode.KeepAnchor, 1)
            
            if before in [" ", "\n", "(", "[", "{"]:
                cursor.insertText("«")
            else:
                cursor.insertText("»")

        self.input_area.blockSignals(False)

        current_text = self.input_area.toPlainText()
        self.preview_area.setHtml(self.html_css + markdown(current_text))
        self.calculate_tension(current_text)

    # Images input
    def process_image(self, original_path):
        # Copies the image to the media folder and returns the MD link
        media_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "media")
        if not os.path.exists(media_dir):
            os.makedirs(media_dir)

        # Create a unique name while keeping the extension
        ext = os.path.splitext(original_path)[1]
        new_filename = f"img_{uuid.uuid4().hex[:8]}{ext}"
        target_path = os.path.join(media_dir, new_filename)

        try:
            shutil.copy2(original_path, target_path)
            # Returning a relative path for Markdown
            return f"![image](media/{new_filename})"
        except Exception as e:
            print(f"Ошибка копирования: {e}")
            return None

    def insert_image(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Выбор фото", "", "Images (*.png *.jpg *.jpeg *.gif)")
        if file_path:
            md_link = self.process_image(file_path)
            if md_link:
                self.input_area.insertPlainText(md_link)
                self.on_text_changed()

    # Drag-and-drop
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.accept()
        else:
            event.ignore()

    def dropEvent(self, event):
        files = [u.toLocalFile() for u in event.mimeData().urls()]
        for f in files:
            if f.lower().endswith(('.png', '.jpg', '.jpeg', '.gif')):
                md_link = self.process_image(f)
                if md_link:
                    # Вставляем в место, куда бросили мышку
                    cursor = self.input_area.cursorForPosition(event.position().toPoint())
                    cursor.insertText(md_link)
                    self.on_text_changed()

# Save with version / сохранение с версией
    def save_md(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save Markdown", "", "Markdown (*.md)")
        
        if path:
            # Separate the extension and base name
            base_path, ext = os.path.splitext(path)
            
            # Remove the existing version from the name, if there is one (e.g. file_v1 -> file)
            # Убрать существующую версию из имени, если она есть (файл_v1 -> файл)
            base_path = re.sub(r'_v\d+$', '', base_path)
            
            counter = 1
            final_path = f"{base_path}_v{counter}{ext}"
            
            # Looking for a free version number so as not to overwrite an existing file.
            # Поиск свободного номера версии, чтобы не перезаписать существующий файл
            while os.path.exists(final_path):
                counter += 1
                final_path = f"{base_path}_v{counter}{ext}"
            
            try:
                with open(final_path, 'w', encoding='utf-8') as f:
                    f.write(self.input_area.toPlainText())
                
                # Inform the user about the exact name of the saved file
                # Информирует пользователя о точном имени сохраненного файла
                self.stats_label.setText(f"SAVED AS: {os.path.basename(final_path)} | WORDS: {len(self.input_area.toPlainText().split())}")
                
            except Exception as e:
                QMessageBox.critical(self, "Error", f"Could not save file: {str(e)}")

if __name__ == "__main__":
    os.environ["GTK_THEME"] = "Adwaita:dark"
    app = QApplication(sys.argv)
    app.setStyle("Fusion") 
    window = LexicalEditor()
    window.show()
    sys.exit(app.exec())
