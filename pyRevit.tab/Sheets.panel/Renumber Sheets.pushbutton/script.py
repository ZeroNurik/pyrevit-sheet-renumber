# -*- coding: utf-8 -*-
"""Flexible and safe batch renumbering for Revit sheets."""

import ctypes
import re

import clr

clr.AddReference("System.Drawing")
clr.AddReference("System.Windows.Forms")

from System import Guid, IntPtr
from System.Drawing import Color, Point, Size
from System.Windows.Forms import (
    AnchorStyles,
    Button,
    CheckBox,
    ComboBox,
    ComboBoxStyle,
    Cursor,
    DataObject,
    DialogResult,
    DragDropEffects,
    Form,
    FormBorderStyle,
    FormStartPosition,
    HorizontalAlignment,
    Label,
    ListView,
    ListViewGroup,
    ListViewItem,
    MessageBox,
    MessageBoxButtons,
    MessageBoxIcon,
    MouseButtons,
    NumericUpDown,
    Panel,
    TextBox,
    Timer,
    View,
)

from pyrevit import DB, forms, revit, script


try:
    text_type = unicode
except NameError:
    text_type = str


WINDOW_TITLE = u"Перенумерация листов"
SHEET_GROUP_PARAMETER_GUID = Guid("7f0c8590-53a2-4ddf-aaed-c70eb9b687bc")
SHEET_GROUP_PARAMETER_NAMES = (
    u"ADSK_Штамп Раздел проекта",
    u"ADSK_Штамп Раздела Проекта",
)
ALL_GROUPS_LABEL = u"Все группы"
UNGROUPED_LABEL = u"(Без раздела)"

LVM_SCROLL = 0x1014
LVM_GETHEADER = 0x101F
LVM_SETEXTENDEDLISTVIEWSTYLE = 0x1036
LVS_EX_DOUBLEBUFFER = 0x00010000
GROUP_DRAG_FORMAT = "PyRevit.SheetRenumber.Group"
WM_NCHITTEST = 0x0084
HTTRANSPARENT = -1


class NativeRect(ctypes.Structure):
    _fields_ = [
        ("Left", ctypes.c_long),
        ("Top", ctypes.c_long),
        ("Right", ctypes.c_long),
        ("Bottom", ctypes.c_long),
    ]


class DropIndicatorPanel(Panel):
    """Overlay that never steals drag events from the native ListView."""

    def WndProc(self, message):
        if message.Msg == WM_NCHITTEST:
            message.Result = IntPtr(HTTRANSPARENT)
            return
        Panel.WndProc(self, message)

try:
    _send_message = ctypes.windll.user32.SendMessageW
    _send_message.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint,
        ctypes.c_size_t,
        ctypes.c_ssize_t,
    ]
    _send_message.restype = ctypes.c_ssize_t
except Exception:
    _send_message = None

try:
    _get_window_rect = ctypes.windll.user32.GetWindowRect
    _get_window_rect.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(NativeRect),
    ]
    _get_window_rect.restype = ctypes.c_int
except Exception:
    _get_window_rect = None

__authors__ = [u"OpenAI Codex", u"Nurgaliy Turlybekov"]


def to_text(value):
    if value is None:
        return u""
    try:
        return text_type(value)
    except Exception:
        return text_type(str(value))


def natural_key(value):
    """Return an IronPython-safe natural sort key."""
    parts = re.split(r"(\d+)", to_text(value).lower())
    key = []
    for part in parts:
        if part.isdigit():
            key.append((0, int(part)))
        else:
            key.append((1, part))
    return key


def element_id_value(element_id):
    """Read ElementId safely across Revit versions."""
    try:
        return int(element_id.Value)
    except Exception:
        return int(element_id.IntegerValue)


def get_all_sheets(doc):
    return list(
        DB.FilteredElementCollector(doc)
        .OfClass(DB.ViewSheet)
        .WhereElementIsNotElementType()
        .ToElements()
    )


def get_sheet_group_parameter(sheet):
    """Return the shared project-section parameter for a sheet."""
    try:
        parameter = sheet.get_Parameter(SHEET_GROUP_PARAMETER_GUID)
        if parameter is not None:
            return parameter
    except Exception:
        pass

    for parameter_name in SHEET_GROUP_PARAMETER_NAMES:
        try:
            parameters = list(sheet.GetParameters(parameter_name))
        except Exception:
            parameters = []
        if parameters:
            return parameters[0]
    return None


def parameter_text(parameter):
    if parameter is None:
        return u""
    try:
        value = parameter.AsString()
    except Exception:
        value = None
    if value is None:
        try:
            value = parameter.AsValueString()
        except Exception:
            value = None
    return to_text(value).strip()


def get_sheet_group_value(sheet):
    return parameter_text(get_sheet_group_parameter(sheet))


def group_display_name(group_value):
    group_value = to_text(group_value).strip()
    return group_value if group_value else UNGROUPED_LABEL


def group_sort_key(group_value):
    group_value = to_text(group_value).strip()
    return (1 if not group_value else 0, natural_key(group_value))


def scroll_list_view_pixels(list_view, vertical_offset):
    """Scroll the native ListView while preserving grouped visual order."""
    if _send_message is None or list_view is None:
        return False
    try:
        if not list_view.IsHandleCreated or not vertical_offset:
            return False
        result = _send_message(
            ctypes.c_void_p(list_view.Handle.ToInt64()),
            LVM_SCROLL,
            0,
            int(vertical_offset),
        )
        return bool(result)
    except Exception:
        return False


def list_view_header_height(list_view):
    """Return the native column-header height in ListView client pixels."""
    if _send_message is None or _get_window_rect is None or list_view is None:
        return 0
    try:
        if not list_view.IsHandleCreated:
            return 0
        header_handle = _send_message(
            ctypes.c_void_p(list_view.Handle.ToInt64()),
            LVM_GETHEADER,
            0,
            0,
        )
        if not header_handle:
            return 0
        header_rect = NativeRect()
        if not _get_window_rect(
            ctypes.c_void_p(header_handle),
            ctypes.byref(header_rect),
        ):
            return 0
        return max(0, int(header_rect.Bottom - header_rect.Top))
    except Exception:
        return 0


def enable_list_view_double_buffering(list_view):
    """Reduce marker flicker while the native grouped list is scrolling."""
    if _send_message is None or list_view is None:
        return False
    try:
        if not list_view.IsHandleCreated:
            return False
        _send_message(
            ctypes.c_void_p(list_view.Handle.ToInt64()),
            LVM_SETEXTENDEDLISTVIEWSTYLE,
            LVS_EX_DOUBLEBUFFER,
            LVS_EX_DOUBLEBUFFER,
        )
        return True
    except Exception:
        return False


def scroll_list_view_line(list_view, direction):
    """Scroll the native ListView by one visual line, including groups."""
    if list_view is None:
        return False
    try:
        row_height = 20
        for item in list_view.Items:
            if item.Bounds.Height > 0:
                row_height = item.Bounds.Height
                break
        return scroll_list_view_pixels(
            list_view,
            -row_height if direction < 0 else row_height,
        )
    except Exception:
        return False


def format_sheet_number(prefix, value, digits, suffix):
    return u"{}{}{}".format(prefix, to_text(value).zfill(digits), suffix)


def sort_items_by_groups(
    items,
    rules,
    reverse_names=False,
    group_value_getter=None,
    sheet_name_getter=None,
):
    """Sort sheets inside project sections using the configured rules."""
    if group_value_getter is None:
        group_value_getter = lambda item: get_sheet_group_value(item.Tag)
    if sheet_name_getter is None:
        sheet_name_getter = lambda item: to_text(item.Tag.Name)

    sort_key = lambda item: natural_key(sheet_name_getter(item))
    buckets = [[] for rule in rules]
    unmatched = []

    for item in items:
        item_group = to_text(group_value_getter(item)).strip()
        sheet_name = to_text(sheet_name_getter(item)).lower()
        matched_index = None
        for index, rule in enumerate(rules):
            rule_group = rule[0]
            group_matches = (
                rule_group is None
                or to_text(rule_group).strip() == item_group
            )
            if group_matches and to_text(rule[1]).lower() in sheet_name:
                matched_index = index
                break
        if matched_index is None:
            unmatched.append(item)
        else:
            buckets[matched_index].append(item)

    ordered = []
    for bucket in buckets:
        bucket.sort(key=sort_key, reverse=reverse_names)
        ordered.extend(bucket)
    unmatched.sort(key=sort_key, reverse=reverse_names)
    ordered.extend(unmatched)
    return ordered


def build_priority_displacement_pairs(selected_pairs, occupied_by_number):
    """Move conflicting unselected sheets into numbers released by selection."""
    selected_new_numbers = set([pair["new"] for pair in selected_pairs])
    released_numbers = [
        pair["old"]
        for pair in selected_pairs
        if pair["old"] not in selected_new_numbers
    ]
    conflict_sheets = []
    conflict_ids = set()
    for pair in selected_pairs:
        conflict_sheet = occupied_by_number.get(pair["new"])
        if conflict_sheet is None:
            continue
        conflict_id = element_id_value(conflict_sheet.Id)
        if conflict_id in conflict_ids:
            continue
        conflict_ids.add(conflict_id)
        conflict_sheets.append(conflict_sheet)

    if len(conflict_sheets) > len(released_numbers):
        raise Exception(
            u"Не удалось безопасно освободить занятые номера листов."
        )

    return [
        {
            "sheet": sheet,
            "old": to_text(sheet.SheetNumber),
            "new": released_number,
            "displaced": True,
        }
        for sheet, released_number in zip(conflict_sheets, released_numbers)
    ]


def move_items_as_block(items, dragged_items, target_index):
    """Move selected items as one ordered block to an insertion boundary."""
    source_indices = [items.index(item) for item in dragged_items]
    removed_before_target = len(
        [index for index in source_indices if index < target_index]
    )
    target_index -= removed_before_target

    remaining_items = [
        item for item in items if item not in dragged_items
    ]
    for offset, item in enumerate(dragged_items):
        remaining_items.insert(target_index + offset, item)
    return remaining_items


def should_drop_after(items, dragged_items, target, cursor_after):
    """Make multi-row downward drops land after the hovered row."""
    if len(dragged_items) > 1:
        source_indices = [items.index(item) for item in dragged_items]
        target_index = items.index(target)
        if target_index > max(source_indices):
            return True
        if target_index < min(source_indices):
            return False
    return cursor_after


def group_destination_index(items, dragged_items, anchor_index, target_index):
    """Map cursor row movement directly to the group's new top row."""
    if len(dragged_items) <= 1 or anchor_index is None:
        return None

    source_indices = [items.index(item) for item in dragged_items]
    source_start = min(source_indices)
    delta = target_index - anchor_index
    max_start = len(items) - len(dragged_items)
    return max(0, min(max_start, source_start + delta))


def move_group_to_index(items, dragged_items, destination_index):
    """Move a group so its top row lands at the requested final index."""
    remaining_items = [
        item for item in items if item not in dragged_items
    ]
    destination_index = max(
        0,
        min(len(remaining_items), destination_index),
    )
    for offset, item in enumerate(dragged_items):
        remaining_items.insert(destination_index + offset, item)
    return remaining_items


class AutoSortSettingsForm(Form):
    """Collect up to five section-aware sheet-name filters."""

    MAX_GROUPS = 5

    def __init__(self, rules, available_groups):
        Form.__init__(self)
        self.rules = []
        self.available_groups = list(available_groups)
        self.group_selectors = []
        self.filter_boxes = []

        self.Text = u"Настройка автосортировки"
        self.StartPosition = FormStartPosition.CenterParent
        self.FormBorderStyle = FormBorderStyle.FixedDialog
        self.MinimizeBox = False
        self.MaximizeBox = False
        self.ShowInTaskbar = False
        self.ClientSize = Size(860, 390)

        description = Label()
        description.Text = (
            u"Задайте до 5 правил. В каждом разделе листы проверяются "
            u"сверху вниз; пустые фильтры не используются."
        )
        description.Location = Point(12, 12)
        description.Size = Size(830, 38)
        self.Controls.Add(description)

        group_header = Label()
        group_header.Text = u"Раздел листов"
        group_header.Location = Point(48, 55)
        group_header.AutoSize = True
        self.Controls.Add(group_header)

        filter_header = Label()
        filter_header.Text = u"Текст, содержащийся в названии листа"
        filter_header.Location = Point(300, 55)
        filter_header.AutoSize = True
        self.Controls.Add(filter_header)

        for index in range(self.MAX_GROUPS):
            row_y = 82 + index * 43

            number_label = Label()
            number_label.Text = to_text(index + 1)
            number_label.Location = Point(16, row_y + 4)
            number_label.Size = Size(25, 25)
            self.Controls.Add(number_label)

            group_selector = ComboBox()
            group_selector.Location = Point(48, row_y)
            group_selector.Size = Size(230, 25)
            group_selector.DropDownStyle = ComboBoxStyle.DropDownList
            group_selector.Items.Add(ALL_GROUPS_LABEL)
            for group_value in self.available_groups:
                group_selector.Items.Add(group_display_name(group_value))
            group_selector.SelectedIndex = 0
            self.Controls.Add(group_selector)
            self.group_selectors.append(group_selector)

            filter_box = TextBox()
            filter_box.Location = Point(300, row_y)
            filter_box.Size = Size(542, 25)
            self.Controls.Add(filter_box)
            self.filter_boxes.append(filter_box)

            if index < len(rules):
                selected_group = rules[index][0]
                if selected_group is None:
                    group_selector.SelectedIndex = 0
                elif selected_group in self.available_groups:
                    group_selector.SelectedIndex = (
                        self.available_groups.index(selected_group) + 1
                    )
                filter_box.Text = rules[index][1]

        note = Label()
        note.Text = (
            u"«Все группы» сортирует общий список только по названию листа. "
            u"Совпадение выполняется без учёта регистра."
        )
        note.Location = Point(12, 305)
        note.Size = Size(570, 35)
        self.Controls.Add(note)

        apply_button = Button()
        apply_button.Text = u"Применить сортировку"
        apply_button.Location = Point(660, 342)
        apply_button.Size = Size(182, 34)
        apply_button.Click += self.on_apply
        self.Controls.Add(apply_button)
        self.AcceptButton = apply_button

        cancel_button = Button()
        cancel_button.Text = u"Отмена"
        cancel_button.Location = Point(544, 342)
        cancel_button.Size = Size(106, 34)
        cancel_button.DialogResult = DialogResult.Cancel
        self.Controls.Add(cancel_button)
        self.CancelButton = cancel_button

    def on_apply(self, sender, args):
        del sender, args
        rules = []
        for index in range(self.MAX_GROUPS):
            filter_text = to_text(self.filter_boxes[index].Text).strip()
            if not filter_text:
                continue
            selected_index = self.group_selectors[index].SelectedIndex
            if selected_index <= 0:
                group_value = None
            else:
                group_value = self.available_groups[selected_index - 1]
            rules.append((group_value, filter_text))

        if not rules:
            MessageBox.Show(
                u"Введите текст фильтра хотя бы для одной группы.",
                u"Автосортировка",
                MessageBoxButtons.OK,
                MessageBoxIcon.Warning,
            )
            return

        self.rules = rules
        self.DialogResult = DialogResult.OK
        self.Close()


class RenameValueForm(Form):
    """Small reusable text entry dialog for sheet and section names."""

    def __init__(self, title, prompt, current_value):
        Form.__init__(self)
        self.value = None
        self.Text = title
        self.StartPosition = FormStartPosition.CenterParent
        self.FormBorderStyle = FormBorderStyle.FixedDialog
        self.MinimizeBox = False
        self.MaximizeBox = False
        self.ShowInTaskbar = False
        self.ClientSize = Size(520, 145)

        prompt_label = Label()
        prompt_label.Text = prompt
        prompt_label.Location = Point(12, 12)
        prompt_label.Size = Size(496, 35)
        self.Controls.Add(prompt_label)

        self.value_box = TextBox()
        self.value_box.Location = Point(12, 52)
        self.value_box.Size = Size(496, 25)
        self.value_box.Text = to_text(current_value)
        self.Controls.Add(self.value_box)

        apply_button = Button()
        apply_button.Text = u"OK"
        apply_button.Location = Point(326, 96)
        apply_button.Size = Size(86, 32)
        apply_button.Click += self.on_apply
        self.Controls.Add(apply_button)
        self.AcceptButton = apply_button

        cancel_button = Button()
        cancel_button.Text = u"Отмена"
        cancel_button.Location = Point(422, 96)
        cancel_button.Size = Size(86, 32)
        cancel_button.DialogResult = DialogResult.Cancel
        self.Controls.Add(cancel_button)
        self.CancelButton = cancel_button

        self.Shown += self.on_shown

    def on_shown(self, sender, args):
        del sender, args
        self.value_box.SelectAll()
        self.value_box.Focus()

    def on_apply(self, sender, args):
        del sender, args
        value = to_text(self.value_box.Text).strip()
        if not value:
            MessageBox.Show(
                u"Введите непустое название.",
                self.Text,
                MessageBoxButtons.OK,
                MessageBoxIcon.Warning,
            )
            return
        self.value = value
        self.DialogResult = DialogResult.OK
        self.Close()


class RenumberSheetsForm(Form):
    def __init__(self, doc, sheets, preselected_ids):
        Form.__init__(self)
        self.doc = doc
        self.sheets = sheets
        self.preselected_ids = set(preselected_ids)
        self.preview_pairs = []
        self.preview_displaced_pairs = []
        self.preview_name_changes = []
        self.preview_group_changes = []
        self.preview_errors = []
        self.applied_count = 0
        self._building_list = False
        self._dragged_items = []
        self._drag_anchor_index = None
        self.group_order = []
        self._group_drag_candidate = None
        self._group_drag_start = None
        self._dragged_group_value = None
        self._group_drop_index = None
        self._drag_scroll_direction = 0
        self._sheet_list_header_height = 0
        self._drop_indicator_target_y = None
        self._drop_indicator_animation_timer = Timer()
        self._drop_indicator_animation_timer.Interval = 15
        self._drop_indicator_animation_timer.Tick += (
            self.on_drop_indicator_animation_tick
        )
        self._drag_scroll_timer = Timer()
        self._drag_scroll_timer.Interval = 100
        self._drag_scroll_timer.Tick += self.on_drag_scroll_tick
        self.sort_rules = []
        self.sheet_name_edits = {}
        self.sheet_group_edits = {}

        self.Text = WINDOW_TITLE
        self.StartPosition = FormStartPosition.CenterScreen
        self.FormBorderStyle = FormBorderStyle.Sizable
        self.MinimumSize = Size(900, 720)
        self.Size = Size(1120, 780)

        self._build_controls()
        self.FormClosed += self.on_form_closed
        self._load_sheets()
        self.update_preview()

    def _build_controls(self):
        self.sort_label = Label()
        self.sort_label.Text = u"Автосортировка внутри разделов листов:"
        self.sort_label.Location = Point(12, 10)
        self.sort_label.AutoSize = True
        self.Controls.Add(self.sort_label)

        self.sort_button = Button()
        self.sort_button.Text = u"Настроить автосортировку..."
        self.sort_button.Location = Point(12, 38)
        self.sort_button.Size = Size(225, 28)
        self.sort_button.Click += self.on_configure_sort
        self.Controls.Add(self.sort_button)

        self.sort_status_label = Label()
        self.sort_status_label.Text = u"Правила не настроены"
        self.sort_status_label.Location = Point(250, 43)
        self.sort_status_label.Size = Size(520, 25)
        self.Controls.Add(self.sort_status_label)

        self.reverse_check = CheckBox()
        self.reverse_check.Text = u"Обратный порядок"
        self.reverse_check.Location = Point(795, 42)
        self.reverse_check.AutoSize = True
        self.Controls.Add(self.reverse_check)

        self.sheet_list = ListView()
        self.sheet_list.Location = Point(12, 80)
        self.sheet_list.Size = Size(1080, 405)
        self.sheet_list.Anchor = (
            AnchorStyles.Top
            | AnchorStyles.Bottom
            | AnchorStyles.Left
            | AnchorStyles.Right
        )
        self.sheet_list.View = View.Details
        self.sheet_list.CheckBoxes = True
        self.sheet_list.FullRowSelect = True
        self.sheet_list.GridLines = True
        self.sheet_list.MultiSelect = True
        self.sheet_list.HideSelection = False
        self.sheet_list.AllowDrop = True
        self.sheet_list.ShowGroups = True
        self.sheet_list.Columns.Add(u"Текущий", 115, HorizontalAlignment.Left)
        self.sheet_list.Columns.Add(u"Новый", 115, HorizontalAlignment.Left)
        self.sheet_list.Columns.Add(u"Название листа", 810, HorizontalAlignment.Left)
        self.sheet_list.ItemChecked += self.on_item_checked
        self.sheet_list.ItemDrag += self.on_item_drag
        self.sheet_list.DragEnter += self.on_drag_enter
        self.sheet_list.DragOver += self.on_drag_over
        self.sheet_list.DragLeave += self.on_drag_leave
        self.sheet_list.DragDrop += self.on_drag_drop
        self.sheet_list.MouseDown += self.on_sheet_list_mouse_down
        self.sheet_list.MouseMove += self.on_sheet_list_mouse_move
        self.sheet_list.MouseUp += self.on_sheet_list_mouse_up
        self.sheet_list.HandleCreated += self.on_sheet_list_handle_created
        self.Controls.Add(self.sheet_list)
        if self.sheet_list.IsHandleCreated:
            self.on_sheet_list_handle_created(self.sheet_list, None)

        self.drop_indicator = DropIndicatorPanel()
        self.drop_indicator.BackColor = Color.Black
        self.drop_indicator.Height = 2
        self.drop_indicator.Visible = False
        self.drop_indicator.Enabled = False
        self.sheet_list.Controls.Add(self.drop_indicator)

        self.select_all_button = Button()
        self.select_all_button.Text = u"Выбрать все"
        self.select_all_button.Location = Point(12, 494)
        self.select_all_button.Size = Size(105, 28)
        self.select_all_button.Anchor = AnchorStyles.Bottom | AnchorStyles.Left
        self.select_all_button.Click += self.on_select_all
        self.Controls.Add(self.select_all_button)

        self.select_none_button = Button()
        self.select_none_button.Text = u"Снять все"
        self.select_none_button.Location = Point(123, 494)
        self.select_none_button.Size = Size(100, 28)
        self.select_none_button.Anchor = AnchorStyles.Bottom | AnchorStyles.Left
        self.select_none_button.Click += self.on_select_none
        self.Controls.Add(self.select_none_button)

        self.move_up_button = Button()
        self.move_up_button.Text = u"Вверх"
        self.move_up_button.Location = Point(240, 494)
        self.move_up_button.Size = Size(90, 28)
        self.move_up_button.Anchor = AnchorStyles.Bottom | AnchorStyles.Left
        self.move_up_button.Click += self.on_move_up
        self.Controls.Add(self.move_up_button)

        self.move_down_button = Button()
        self.move_down_button.Text = u"Вниз"
        self.move_down_button.Location = Point(336, 494)
        self.move_down_button.Size = Size(90, 28)
        self.move_down_button.Anchor = AnchorStyles.Bottom | AnchorStyles.Left
        self.move_down_button.Click += self.on_move_down
        self.Controls.Add(self.move_down_button)

        self.selected_label = Label()
        self.selected_label.Text = u"Выбрано: 0"
        self.selected_label.Location = Point(445, 499)
        self.selected_label.AutoSize = True
        self.selected_label.Anchor = AnchorStyles.Bottom | AnchorStyles.Left
        self.Controls.Add(self.selected_label)

        caption_y = 538
        input_y = 570

        self.prefix_label = Label()
        self.prefix_label.Text = u"Префикс"
        self.prefix_label.Location = Point(12, caption_y)
        self.prefix_label.AutoSize = True
        self.prefix_label.Anchor = AnchorStyles.Bottom | AnchorStyles.Left
        self.Controls.Add(self.prefix_label)

        self.prefix_box = TextBox()
        self.prefix_box.Location = Point(12, input_y)
        self.prefix_box.Size = Size(180, 25)
        self.prefix_box.Anchor = AnchorStyles.Bottom | AnchorStyles.Left
        self.prefix_box.TextChanged += self.on_setting_changed
        self.Controls.Add(self.prefix_box)

        self.start_label = Label()
        self.start_label.Text = u"Старт"
        self.start_label.Location = Point(210, caption_y)
        self.start_label.AutoSize = True
        self.start_label.Anchor = AnchorStyles.Bottom | AnchorStyles.Left
        self.Controls.Add(self.start_label)

        self.start_box = NumericUpDown()
        self.start_box.Location = Point(210, input_y)
        self.start_box.Size = Size(100, 25)
        self.start_box.Minimum = 0
        self.start_box.Maximum = 999999999
        self.start_box.Value = 1
        self.start_box.Anchor = AnchorStyles.Bottom | AnchorStyles.Left
        self.start_box.ValueChanged += self.on_setting_changed
        self.Controls.Add(self.start_box)

        self.step_label = Label()
        self.step_label.Text = u"Шаг"
        self.step_label.Location = Point(330, caption_y)
        self.step_label.AutoSize = True
        self.step_label.Anchor = AnchorStyles.Bottom | AnchorStyles.Left
        self.Controls.Add(self.step_label)

        self.step_box = NumericUpDown()
        self.step_box.Location = Point(330, input_y)
        self.step_box.Size = Size(100, 25)
        self.step_box.Minimum = 1
        self.step_box.Maximum = 999999
        self.step_box.Value = 1
        self.step_box.Anchor = AnchorStyles.Bottom | AnchorStyles.Left
        self.step_box.ValueChanged += self.on_setting_changed
        self.Controls.Add(self.step_box)

        self.digits_label = Label()
        self.digits_label.Text = u"Разрядов"
        self.digits_label.Location = Point(450, caption_y)
        self.digits_label.AutoSize = True
        self.digits_label.Anchor = AnchorStyles.Bottom | AnchorStyles.Left
        self.Controls.Add(self.digits_label)

        self.digits_box = NumericUpDown()
        self.digits_box.Location = Point(450, input_y)
        self.digits_box.Size = Size(100, 25)
        self.digits_box.Minimum = 1
        self.digits_box.Maximum = 12
        self.digits_box.Value = 3
        self.digits_box.Anchor = AnchorStyles.Bottom | AnchorStyles.Left
        self.digits_box.ValueChanged += self.on_setting_changed
        self.Controls.Add(self.digits_box)

        self.suffix_label = Label()
        self.suffix_label.Text = u"Суффикс"
        self.suffix_label.Location = Point(570, caption_y)
        self.suffix_label.AutoSize = True
        self.suffix_label.Anchor = AnchorStyles.Bottom | AnchorStyles.Left
        self.Controls.Add(self.suffix_label)

        self.suffix_box = TextBox()
        self.suffix_box.Location = Point(570, input_y)
        self.suffix_box.Size = Size(180, 25)
        self.suffix_box.Anchor = AnchorStyles.Bottom | AnchorStyles.Left
        self.suffix_box.TextChanged += self.on_setting_changed
        self.Controls.Add(self.suffix_box)

        self.skip_occupied_check = CheckBox()
        self.skip_occupied_check.Text = (
            u"Пропускать занятые номера (снять — приоритет выбранным листам)"
        )
        self.skip_occupied_check.Location = Point(12, 606)
        self.skip_occupied_check.Size = Size(1080, 34)
        self.skip_occupied_check.AutoSize = False
        self.skip_occupied_check.Checked = True
        self.skip_occupied_check.Anchor = (
            AnchorStyles.Bottom | AnchorStyles.Left | AnchorStyles.Right
        )
        self.skip_occupied_check.CheckedChanged += self.on_setting_changed
        self.Controls.Add(self.skip_occupied_check)

        self.status_label = Label()
        self.status_label.Text = u""
        self.status_label.Location = Point(12, 650)
        self.status_label.Size = Size(790, 45)
        self.status_label.Anchor = (
            AnchorStyles.Bottom | AnchorStyles.Left | AnchorStyles.Right
        )
        self.Controls.Add(self.status_label)

        self.apply_button = Button()
        self.apply_button.Text = u"Применить"
        self.apply_button.Location = Point(862, 665)
        self.apply_button.Size = Size(110, 34)
        self.apply_button.Anchor = AnchorStyles.Bottom | AnchorStyles.Right
        self.apply_button.Click += self.on_apply
        self.Controls.Add(self.apply_button)

        self.cancel_button = Button()
        self.cancel_button.Text = u"Отмена"
        self.cancel_button.Location = Point(982, 665)
        self.cancel_button.Size = Size(110, 34)
        self.cancel_button.Anchor = AnchorStyles.Bottom | AnchorStyles.Right
        self.cancel_button.DialogResult = DialogResult.Cancel
        self.Controls.Add(self.cancel_button)
        self.CancelButton = self.cancel_button

    def _load_sheets(self):
        ordered = sorted(
            self.sheets,
            key=lambda sheet: (
                group_sort_key(self._sheet_group_value(sheet)),
                natural_key(sheet.SheetNumber),
                natural_key(self._sheet_name_value(sheet)),
            ),
        )
        self._building_list = True
        try:
            self.sheet_list.Items.Clear()
            self.sheet_list.Groups.Clear()
            list_groups = self._create_list_groups(ordered)
            for sheet in ordered:
                current_number = to_text(sheet.SheetNumber)
                sheet_name = self._sheet_name_value(sheet)
                if getattr(sheet, "IsPlaceholder", False):
                    sheet_name = u"{} [заполнитель]".format(sheet_name)
                item = ListViewItem(current_number)
                item.SubItems.Add(u"")
                item.SubItems.Add(sheet_name)
                item.Tag = sheet
                item.Group = list_groups[self._sheet_group_value(sheet)]
                item.Checked = element_id_value(sheet.Id) in self.preselected_ids
                self.sheet_list.Items.Add(item)
        finally:
            self._building_list = False

        self._update_sort_status()

    def _sheet_name_value(self, sheet):
        sheet_id = element_id_value(sheet.Id)
        return self.sheet_name_edits.get(sheet_id, to_text(sheet.Name))

    def _sheet_group_value(self, sheet):
        sheet_id = element_id_value(sheet.Id)
        return self.sheet_group_edits.get(
            sheet_id,
            get_sheet_group_value(sheet),
        )

    def _available_group_values(self):
        values = set([self._sheet_group_value(sheet) for sheet in self.sheets])
        return self._ordered_group_values(values)

    def _ordered_group_values(self, group_values):
        values = set(group_values)
        ordered = []
        seen = set()
        for group_value in self.group_order:
            if group_value in values and group_value not in seen:
                ordered.append(group_value)
                seen.add(group_value)
        for group_value in sorted(values, key=group_sort_key):
            if group_value not in seen:
                ordered.append(group_value)
                seen.add(group_value)
        self.group_order = list(ordered)
        return ordered

    def _create_list_groups(self, sheets_or_items):
        group_values = set()
        for value in sheets_or_items:
            sheet = value.Tag if isinstance(value, ListViewItem) else value
            group_values.add(self._sheet_group_value(sheet))

        list_groups = {}
        self._list_group_values = {}
        for index, group_value in enumerate(
            self._ordered_group_values(group_values)
        ):
            list_group = ListViewGroup(group_display_name(group_value))
            list_group.Name = u"sheet_group_{}".format(index)
            self.sheet_list.Groups.Add(list_group)
            list_groups[group_value] = list_group
            self._list_group_values[list_group.Name] = group_value
        return list_groups

    def _group_value_from_list_group(self, list_group):
        if list_group is None:
            return None
        return self._list_group_values.get(to_text(list_group.Name))

    def _list_group_ranges(self):
        group_ranges = []
        for list_group in self.sheet_list.Groups:
            group_items = [group_item for group_item in list_group.Items]
            if not group_items:
                continue
            first_item = min(group_items, key=lambda value: value.Bounds.Top)
            last_item = max(group_items, key=lambda value: value.Bounds.Bottom)
            group_value = self._group_value_from_list_group(list_group)
            group_ranges.append(
                (
                    first_item.Bounds.Top,
                    last_item.Bounds.Bottom,
                    group_value,
                )
            )

        group_ranges.sort(key=lambda value: value[0])
        return group_ranges

    def _group_header_value_at_point(self, point):
        group_ranges = self._list_group_ranges()
        previous_bottom = 0
        for top, bottom, group_value in group_ranges:
            header_top = max(previous_bottom, top - 64)
            if header_top <= point.Y < top:
                return group_value
            previous_bottom = max(previous_bottom, bottom)
        return None

    def _group_value_at_point(self, point):
        header_group = self._group_header_value_at_point(point)
        if header_group is not None:
            return header_group

        item = self._item_at_y(point.Y)
        if item is not None:
            return self._sheet_group_value(item.Tag)

        group_ranges = self._list_group_ranges()
        for top, bottom, group_value in group_ranges:
            if top <= point.Y <= bottom + 8:
                return group_value
        if not group_ranges:
            return None
        if point.Y < group_ranges[0][0]:
            return group_ranges[0][2]
        return group_ranges[-1][2]

    def _group_items(self, group_value, items=None):
        if items is None:
            items = self._items()
        return [
            item
            for item in items
            if self._sheet_group_value(item.Tag) == group_value
        ]

    def _visual_items(self):
        items = []
        for list_group in self.sheet_list.Groups:
            items.extend([item for item in list_group.Items])
        return items

    def _set_sheet_group_edit(self, sheet, group_value):
        sheet_id = element_id_value(sheet.Id)
        original_group = get_sheet_group_value(sheet)
        if group_value == original_group:
            self.sheet_group_edits.pop(sheet_id, None)
        else:
            self.sheet_group_edits[sheet_id] = group_value

    def _order_items_by_sections(self, items):
        buckets = {}
        for item in items:
            group_value = self._sheet_group_value(item.Tag)
            buckets.setdefault(group_value, []).append(item)
        ordered = []
        for group_value in self._ordered_group_values(buckets.keys()):
            ordered.extend(buckets[group_value])
        return ordered

    def _update_sort_status(self):
        section_count = len(self._available_group_values())
        if not self.sort_rules:
            self.sort_status_label.Text = u"Разделов: {}; правила не настроены".format(
                section_count
            )
            return
        self.sort_status_label.Text = u"Разделов: {}; правил: {}".format(
            section_count,
            len(self.sort_rules),
        )

    def _items(self):
        return [item for item in self.sheet_list.Items]

    def _item_at_y(self, y, items=None):
        """Find a visual row by Y, independent of columns and empty X space."""
        if items is None:
            items = self._items()
        visible_items = []
        for item in items:
            bounds = item.Bounds
            if bounds.Height <= 0:
                continue
            visible_items.append(item)
            if bounds.Top <= y < bounds.Bottom:
                return item

        # Native ListView grid lines can leave a one-pixel gap where no item
        # owns the cursor. Treat a small gap as part of the nearest row instead
        # of briefly falling back to the bottom of the group.
        nearest_item = None
        nearest_distance = None
        for item in visible_items:
            bounds = item.Bounds
            if y < bounds.Top:
                distance = bounds.Top - y
            else:
                distance = y - bounds.Bottom + 1
            if nearest_distance is None or distance < nearest_distance:
                nearest_item = item
                nearest_distance = distance
        if nearest_distance is not None and nearest_distance <= 3:
            return nearest_item
        return None

    def _checked_items(self):
        return [item for item in self.sheet_list.Items if item.Checked]

    def _rebuild_items(self, items):
        selected_ids = set(
            element_id_value(item.Tag.Id)
            for item in self.sheet_list.SelectedItems
        )
        dragged_ids = set(
            element_id_value(item.Tag.Id)
            for item in self._dragged_items
        )
        anchor_excluded_ids = selected_ids.union(dragged_ids)
        viewport_anchor_id = None
        viewport_anchor_top = 0
        visible_items = sorted(
            [
                item
                for item in self.sheet_list.Items
                if item.Bounds.Bottom > 0
                and item.Bounds.Top < self.sheet_list.ClientSize.Height
            ],
            key=lambda value: value.Bounds.Top,
        )
        anchor_candidates = [
            item
            for item in visible_items
            if element_id_value(item.Tag.Id) not in anchor_excluded_ids
        ]
        if not anchor_candidates:
            anchor_candidates = visible_items
        if anchor_candidates:
            anchor_item = anchor_candidates[0]
            viewport_anchor_id = element_id_value(anchor_item.Tag.Id)
            viewport_anchor_top = anchor_item.Bounds.Top

        focused_set = False
        self._building_list = True
        self.sheet_list.BeginUpdate()
        try:
            self.sheet_list.Items.Clear()
            self.sheet_list.Groups.Clear()
            list_groups = self._create_list_groups(items)
            for item in items:
                item.Group = list_groups[self._sheet_group_value(item.Tag)]
                self.sheet_list.Items.Add(item)
                if element_id_value(item.Tag.Id) in selected_ids:
                    item.Selected = True
                    if not focused_set:
                        item.Focused = True
                        focused_set = True
        finally:
            self._building_list = False
            self.sheet_list.EndUpdate()

        if self.sheet_list.Items.Count and viewport_anchor_id is not None:
            viewport_anchor = next(
                (
                    item
                    for item in self.sheet_list.Items
                    if element_id_value(item.Tag.Id) == viewport_anchor_id
                ),
                None,
            )
            if viewport_anchor is not None:
                try:
                    viewport_anchor.EnsureVisible()
                    self.sheet_list.Update()
                    vertical_offset = (
                        viewport_anchor.Bounds.Top - viewport_anchor_top
                    )
                    if vertical_offset:
                        scroll_list_view_pixels(
                            self.sheet_list,
                            vertical_offset,
                        )
                    self.sheet_list.Update()
                except Exception:
                    viewport_anchor.EnsureVisible()
        elif self.sheet_list.Items.Count:
            try:
                self.sheet_list.Items[0].EnsureVisible()
            except Exception:
                pass
        self.update_preview()

    def on_configure_sort(self, sender, args):
        del sender, args
        settings_form = AutoSortSettingsForm(
            self.sort_rules,
            self._available_group_values(),
        )
        try:
            if settings_form.ShowDialog(self) != DialogResult.OK:
                return
            self.sort_rules = list(settings_form.rules)
        finally:
            settings_form.Dispose()

        ordered = sort_items_by_groups(
            self._items(),
            self.sort_rules,
            self.reverse_check.Checked,
            group_value_getter=lambda item: self._sheet_group_value(item.Tag),
            sheet_name_getter=lambda item: self._sheet_name_value(item.Tag),
        )

        self._update_sort_status()
        self._rebuild_items(ordered)

    def _ask_for_name(self, title, prompt, current_value):
        dialog = RenameValueForm(title, prompt, current_value)
        try:
            if dialog.ShowDialog(self) == DialogResult.OK:
                return dialog.value
        finally:
            dialog.Dispose()
        return None

    def _rename_sheet_item(self, item):
        sheet = item.Tag
        new_name = self._ask_for_name(
            u"Переименование листа",
            u"Новое название листа {}:".format(sheet.SheetNumber),
            self._sheet_name_value(sheet),
        )
        if new_name is None:
            return

        sheet_id = element_id_value(sheet.Id)
        original_name = to_text(sheet.Name)
        if new_name == original_name:
            self.sheet_name_edits.pop(sheet_id, None)
        else:
            self.sheet_name_edits[sheet_id] = new_name
        item.SubItems[2].Text = new_name
        if getattr(sheet, "IsPlaceholder", False):
            item.SubItems[2].Text = u"{} [заполнитель]".format(new_name)
        self.update_preview()

    def _rename_group_value(self, old_group):
        new_group = self._ask_for_name(
            u"Переименование группы листов",
            u"Новое название группы «{}»:".format(
                group_display_name(old_group)
            ),
            old_group,
        )
        if new_group is None or new_group == old_group:
            return

        affected_count = 0
        for sheet in self.sheets:
            if self._sheet_group_value(sheet) != old_group:
                continue
            sheet_id = element_id_value(sheet.Id)
            original_group = get_sheet_group_value(sheet)
            if new_group == original_group:
                self.sheet_group_edits.pop(sheet_id, None)
            else:
                self.sheet_group_edits[sheet_id] = new_group
            affected_count += 1

        updated_rules = []
        for group_value, filter_text in self.sort_rules:
            if group_value == old_group:
                group_value = new_group
            updated_rules.append((group_value, filter_text))
        self.sort_rules = updated_rules
        self.group_order = [
            new_group if group_value == old_group else group_value
            for group_value in self.group_order
        ]

        ordered = self._order_items_by_sections(self._items())
        self._update_sort_status()
        self._rebuild_items(ordered)
        self.status_label.ForeColor = Color.DimGray
        self.status_label.Text = u"Группа переименована для листов: {}.".format(
            affected_count
        )

    def on_sheet_list_mouse_down(self, sender, args):
        del sender
        if args.Button != MouseButtons.Left:
            self._group_drag_candidate = None
            self._group_drag_start = None
            return
        group_value = self._group_header_value_at_point(args.Location)
        if args.Clicks >= 2:
            self._group_drag_candidate = None
            self._group_drag_start = None
            if group_value is not None:
                self._rename_group_value(group_value)
                return
            item = self.sheet_list.GetItemAt(args.X, args.Y)
            if item is not None:
                self._rename_sheet_item(item)
            return

        self._group_drag_candidate = group_value
        self._group_drag_start = args.Location if group_value is not None else None

    def on_sheet_list_mouse_move(self, sender, args):
        del sender
        if (
            args.Button != MouseButtons.Left
            or self._group_drag_candidate is None
            or self._group_drag_start is None
        ):
            return
        if (
            abs(args.X - self._group_drag_start.X) < 6
            and abs(args.Y - self._group_drag_start.Y) < 6
        ):
            return

        self._begin_group_drag(self._group_drag_candidate)

    def _begin_group_drag(self, group_value):
        if group_value is None or self._dragged_group_value is not None:
            return
        self._hide_drop_marker()
        self._group_drag_candidate = None
        self._group_drag_start = None
        self._dragged_group_value = group_value
        self._group_drop_index = None
        self._dragged_items = []
        self._drag_anchor_index = None
        drag_data = DataObject()
        drag_data.SetData(
            GROUP_DRAG_FORMAT,
            group_display_name(group_value),
        )
        try:
            self.sheet_list.DoDragDrop(
                drag_data,
                DragDropEffects.Move,
            )
        finally:
            self._stop_drag_scroll()
            self._hide_drop_marker()
            self._dragged_group_value = None
            self._group_drop_index = None

    def on_sheet_list_mouse_up(self, sender, args):
        del sender, args
        self._group_drag_candidate = None
        self._group_drag_start = None

    def on_select_all(self, sender, args):
        del sender, args
        self._building_list = True
        try:
            for item in self.sheet_list.Items:
                item.Checked = True
        finally:
            self._building_list = False
        self.update_preview()

    def on_select_none(self, sender, args):
        del sender, args
        self._building_list = True
        try:
            for item in self.sheet_list.Items:
                item.Checked = False
        finally:
            self._building_list = False
        self.update_preview()

    def on_item_drag(self, sender, args):
        del sender
        if args.Button != MouseButtons.Left:
            return

        if self._group_drag_candidate is not None:
            self._begin_group_drag(self._group_drag_candidate)
            return

        self._group_drag_candidate = None
        self._group_drag_start = None
        self._hide_drop_marker()

        selected_items = [
            item for item in self.sheet_list.Items if item.Selected
        ]
        if args.Item not in selected_items:
            for item in selected_items:
                item.Selected = False
            args.Item.Selected = True
            selected_items = [args.Item]

        self._dragged_items = selected_items
        self._drag_anchor_index = args.Item.Index
        try:
            self.sheet_list.DoDragDrop(args.Item, DragDropEffects.Move)
        finally:
            self._stop_drag_scroll()
            self._dragged_items = []
            self._drag_anchor_index = None
            self._hide_drop_marker()

    def on_drag_enter(self, sender, args):
        del sender
        if (
            self._is_group_drag_data(args.Data)
            or args.Data.GetDataPresent(ListViewItem)
        ):
            args.Effect = DragDropEffects.Move
        else:
            args.Effect = getattr(DragDropEffects, "None")

    def _is_group_drag_data(self, data):
        if self._dragged_group_value is None or data is None:
            return False
        try:
            return data.GetDataPresent(GROUP_DRAG_FORMAT)
        except Exception:
            return False

    def on_sheet_list_handle_created(self, sender, args):
        del args
        enable_list_view_double_buffering(sender)
        self._sheet_list_header_height = list_view_header_height(sender)

    def _list_content_top(self):
        if self._sheet_list_header_height <= 0:
            self._sheet_list_header_height = list_view_header_height(
                self.sheet_list
            )
        return max(0, self._sheet_list_header_height)

    def _hide_drop_marker(self):
        self.sheet_list.InsertionMark.Index = -1
        self._drop_indicator_target_y = None
        if self._drop_indicator_animation_timer.Enabled:
            self._drop_indicator_animation_timer.Stop()
        if self.drop_indicator.Visible:
            self.drop_indicator.Visible = False

    def on_drop_indicator_animation_tick(self, sender, args):
        del sender, args
        target_y = self._drop_indicator_target_y
        if target_y is None or not self.drop_indicator.Visible:
            self._drop_indicator_animation_timer.Stop()
            return

        distance = target_y - self.drop_indicator.Top
        if abs(distance) <= 1:
            self.drop_indicator.Top = target_y
            self._drop_indicator_animation_timer.Stop()
            return

        step = max(1, min(6, int(abs(distance) * 0.35)))
        self.drop_indicator.Top += step if distance > 0 else -step

    def _show_drop_marker(self, item, appears_after):
        if item is None:
            self._hide_drop_marker()
            return
        boundary_y = item.Bounds.Bottom if appears_after else item.Bounds.Top
        self._show_drop_marker_y(boundary_y)

    def _show_drop_marker_y(self, boundary_y):
        content_top = self._list_content_top()
        boundary_y = max(
            content_top,
            min(self.sheet_list.ClientSize.Height - 2, boundary_y),
        )
        marker_width = self.sheet_list.ClientSize.Width
        self._drop_indicator_target_y = boundary_y
        if not self.drop_indicator.Visible:
            self.drop_indicator.SetBounds(0, boundary_y, marker_width, 2)
            self.drop_indicator.Visible = True
            self.drop_indicator.BringToFront()
            return

        if self.drop_indicator.Left != 0:
            self.drop_indicator.Left = 0
        if self.drop_indicator.Width != marker_width:
            self.drop_indicator.Width = marker_width
        if self.drop_indicator.Height != 2:
            self.drop_indicator.Height = 2
        if abs(self.drop_indicator.Top - boundary_y) > 48:
            self.drop_indicator.Top = boundary_y
            if self._drop_indicator_animation_timer.Enabled:
                self._drop_indicator_animation_timer.Stop()
        elif self.drop_indicator.Top != boundary_y:
            if not self._drop_indicator_animation_timer.Enabled:
                self._drop_indicator_animation_timer.Start()
        elif self._drop_indicator_animation_timer.Enabled:
            self._drop_indicator_animation_timer.Stop()

    def _cursor_appears_after(
        self,
        item,
        cursor_y,
        previous_boundary_y=None,
    ):
        """Choose a row boundary with a small midpoint dead zone."""
        bounds = item.Bounds
        midpoint = bounds.Top + bounds.Height // 2
        hysteresis = max(2, min(5, bounds.Height // 5))
        if previous_boundary_y is None and self.drop_indicator.Visible:
            previous_boundary_y = self._drop_indicator_target_y

        if previous_boundary_y is not None:
            if abs(previous_boundary_y - bounds.Top) <= 1:
                return cursor_y > midpoint + hysteresis
            if abs(previous_boundary_y - bounds.Bottom) <= 1:
                return cursor_y >= midpoint - hysteresis
        return cursor_y > midpoint

    def _group_drop_boundaries(self):
        group_ranges = self._list_group_ranges()
        if not group_ranges:
            return []

        content_top = self._list_content_top()
        content_bottom = self.sheet_list.ClientSize.Height - 2
        visible_indexes = [
            index
            for index, group_range in enumerate(group_ranges)
            if group_range[1] >= content_top
            and group_range[0] <= content_bottom
        ]
        if not visible_indexes:
            return []

        first_index = visible_indexes[0]
        last_index = visible_indexes[-1]
        boundaries = [(first_index, content_top)]
        for index in range(first_index + 1, last_index + 1):
            previous_bottom = group_ranges[index - 1][1]
            current_top = group_ranges[index][0]
            boundary_y = int((previous_bottom + current_top) / 2)
            if content_top <= boundary_y <= content_bottom:
                boundaries.append((index, boundary_y))
        boundaries.append((last_index + 1, content_bottom))
        return boundaries

    def _update_group_drag_marker(self, point):
        boundaries = self._group_drop_boundaries()
        if not boundaries:
            self._group_drop_index = None
            self._hide_drop_marker()
            return
        destination_index, boundary_y = min(
            boundaries,
            key=lambda value: abs(point.Y - value[1]),
        )
        self._group_drop_index = destination_index
        self._show_drop_marker_y(boundary_y)

    def _move_group_to_index(self, group_value, destination_index):
        group_values = self._ordered_group_values(
            [self._sheet_group_value(sheet) for sheet in self.sheets]
        )
        if group_value not in group_values or destination_index is None:
            return

        old_index = group_values.index(group_value)
        destination_index = max(0, min(destination_index, len(group_values)))
        group_values.pop(old_index)
        if destination_index > old_index:
            destination_index -= 1
        destination_index = max(0, min(destination_index, len(group_values)))
        group_values.insert(destination_index, group_value)
        if group_values == self.group_order:
            return

        self.group_order = group_values
        member_count = len(
            [
                sheet
                for sheet in self.sheets
                if self._sheet_group_value(sheet) == group_value
            ]
        )
        ordered_items = self._order_items_by_sections(self._items())
        self._rebuild_items(ordered_items)
        self.status_label.ForeColor = Color.DimGray
        self.status_label.Text = (
            u"Группа «{}» перемещена целиком: {} листов. "
            u"Разделы листов не изменены."
        ).format(
            group_display_name(group_value),
            member_count,
        )

    def _show_drop_marker_at_index(self, items, destination_index):
        if not items:
            self._hide_drop_marker()
            return
        if destination_index >= len(items):
            self._show_drop_marker(items[-1], True)
        else:
            destination_index = max(0, destination_index)
            self._show_drop_marker(items[destination_index], False)

    def _update_drag_marker(self, point):
        items = self._items()
        target = self._item_at_y(point.Y, items)
        dragged_items = [
            item for item in self._dragged_items if item in items
        ]
        target_group = self._group_value_at_point(point)

        if target is None and target_group is not None:
            target_group_items = self._group_items(target_group, items)
            if target_group_items:
                first_item = min(
                    target_group_items,
                    key=lambda item: item.Bounds.Top,
                )
                last_item = max(
                    target_group_items,
                    key=lambda item: item.Bounds.Bottom,
                )
                if point.Y < first_item.Bounds.Top:
                    self._show_drop_marker(first_item, False)
                elif point.Y > last_item.Bounds.Bottom:
                    self._show_drop_marker(last_item, True)
                # Inside a group, a transient empty hit is a grid-line gap.
                # Keep the current marker rather than flashing at group end.
                return

        if len(dragged_items) > 1:
            source_groups = set(
                [self._sheet_group_value(item.Tag) for item in dragged_items]
            )
            same_group_move = (
                len(source_groups) == 1 and target_group in source_groups
            )
            if not same_group_move and target is not None:
                cursor_after = self._cursor_appears_after(
                    target,
                    point.Y,
                )
                self._show_drop_marker(target, cursor_after)
                return

            if target is None:
                if items and point.Y < items[0].Bounds.Top:
                    destination_index = 0
                else:
                    destination_index = len(items) - len(dragged_items)
            else:
                destination_index = group_destination_index(
                    items,
                    dragged_items,
                    self._drag_anchor_index,
                    items.index(target),
                )

            if destination_index is None or not items:
                self._hide_drop_marker()
            else:
                self._show_drop_marker_at_index(items, destination_index)
            return

        if target is None:
            if items:
                visible_items = sorted(items, key=lambda item: item.Bounds.Top)
                first_item = visible_items[0]
                last_item = visible_items[-1]
                if point.Y < first_item.Bounds.Top:
                    self._show_drop_marker(first_item, False)
                else:
                    self._show_drop_marker(last_item, True)
            else:
                self._hide_drop_marker()
            return

        if target in dragged_items:
            cursor_after = self._cursor_appears_after(
                target,
                point.Y,
            )
            self._show_drop_marker(target, cursor_after)
            return

        cursor_after = self._cursor_appears_after(
            target,
            point.Y,
        )
        appears_after = should_drop_after(
            items,
            dragged_items,
            target,
            cursor_after,
        )
        self._show_drop_marker(target, appears_after)

    def _stop_drag_scroll(self):
        self._drag_scroll_direction = 0
        if self._drag_scroll_timer.Enabled:
            self._drag_scroll_timer.Stop()

    def _update_drag_scroll(self, point):
        if not self.sheet_list.Items.Count:
            self._stop_drag_scroll()
            return
        if not self.drop_indicator.Visible:
            self._stop_drag_scroll()
            return

        content_top = self._list_content_top()
        threshold = min(88, max(56, self.sheet_list.ClientSize.Height // 7))
        bottom_edge = self.sheet_list.ClientSize.Height
        direction = 0
        distance = threshold

        if point.Y <= content_top + threshold:
            direction = -1
            distance = max(0, point.Y - content_top)
        elif point.Y >= bottom_edge - threshold:
            direction = 1
            distance = max(0, bottom_edge - point.Y)

        if direction == 0:
            self._stop_drag_scroll()
            return

        if distance <= 10:
            interval = 28
        elif distance <= 26:
            interval = 42
        elif distance <= 48:
            interval = 65
        else:
            interval = 95

        self._drag_scroll_direction = direction
        self._drag_scroll_timer.Interval = interval
        if not self._drag_scroll_timer.Enabled:
            self._drag_scroll_timer.Start()

    def on_drag_scroll_tick(self, sender, args):
        del sender, args
        if (
            not self._dragged_items
            and self._dragged_group_value is None
        ) or self._drag_scroll_direction == 0:
            self._stop_drag_scroll()
            return

        try:
            if not scroll_list_view_line(
                self.sheet_list,
                self._drag_scroll_direction,
            ):
                self._stop_drag_scroll()
                return
            self.sheet_list.Update()
            point = self.sheet_list.PointToClient(Cursor.Position)
            if self.sheet_list.ClientRectangle.Contains(point):
                if self._dragged_group_value is not None:
                    self._update_group_drag_marker(point)
                else:
                    self._update_drag_marker(point)
                self._update_drag_scroll(point)
            else:
                self._stop_drag_scroll()
        except Exception:
            self._stop_drag_scroll()

    def on_drag_over(self, sender, args):
        del sender
        if self._is_group_drag_data(args.Data):
            args.Effect = DragDropEffects.Move
            point = self.sheet_list.PointToClient(Point(args.X, args.Y))
            self._update_group_drag_marker(point)
            self._update_drag_scroll(point)
            return
        if not args.Data.GetDataPresent(ListViewItem):
            args.Effect = getattr(DragDropEffects, "None")
            self._stop_drag_scroll()
            self._hide_drop_marker()
            return

        args.Effect = DragDropEffects.Move
        point = self.sheet_list.PointToClient(Point(args.X, args.Y))
        self._update_drag_marker(point)
        self._update_drag_scroll(point)

    def on_drag_leave(self, sender, args):
        del sender, args
        self._stop_drag_scroll()
        self._hide_drop_marker()

    def on_form_closed(self, sender, args):
        del sender, args
        self._stop_drag_scroll()
        self._hide_drop_marker()
        self._drag_scroll_timer.Dispose()
        self._drop_indicator_animation_timer.Dispose()

    def on_drag_drop(self, sender, args):
        del sender
        self._stop_drag_scroll()
        previous_boundary_y = (
            self._drop_indicator_target_y
            if self._drop_indicator_target_y is not None
            else None
        )
        self._hide_drop_marker()
        if self._is_group_drag_data(args.Data):
            self._move_group_to_index(
                self._dragged_group_value,
                self._group_drop_index,
            )
            return
        if not args.Data.GetDataPresent(ListViewItem):
            return

        items = self._items()
        dragged_items = [
            item for item in self._dragged_items if item in items
        ]
        if not dragged_items:
            dragged = args.Data.GetData(ListViewItem)
            if dragged in items:
                dragged_items = [dragged]
        if not dragged_items:
            return

        point = self.sheet_list.PointToClient(Point(args.X, args.Y))
        target = self._item_at_y(point.Y, items)
        target_group = self._group_value_at_point(point)
        if target_group is None:
            return

        source_groups = set(
            [self._sheet_group_value(item.Tag) for item in dragged_items]
        )
        moving_between_groups = (
            len(source_groups) != 1 or target_group not in source_groups
        )

        if moving_between_groups:
            if target is not None and target not in dragged_items:
                target_index = items.index(target)
                if self._cursor_appears_after(
                    target,
                    point.Y,
                    previous_boundary_y,
                ):
                    target_index += 1
            else:
                target_group_items = self._group_items(target_group, items)
                if not target_group_items:
                    target_index = len(items)
                elif point.Y < target_group_items[0].Bounds.Top:
                    target_index = items.index(target_group_items[0])
                else:
                    target_index = items.index(target_group_items[-1]) + 1

            for dragged_item in dragged_items:
                self._set_sheet_group_edit(dragged_item.Tag, target_group)

            remaining_items = move_items_as_block(
                items,
                dragged_items,
                target_index,
            )
            remaining_items = self._order_items_by_sections(remaining_items)
            self._update_sort_status()
            self._rebuild_items(remaining_items)
            self.status_label.ForeColor = Color.DimGray
            self.status_label.Text = u"Листов перемещено в раздел «{}»: {}.".format(
                group_display_name(target_group),
                len(dragged_items),
            )
            return

        if len(dragged_items) > 1:
            if target is None:
                target_group_items = self._group_items(target_group, items)
                if target_group_items and point.Y < target_group_items[0].Bounds.Top:
                    destination_index = items.index(target_group_items[0])
                elif target_group_items:
                    destination_index = items.index(target_group_items[-1]) + 1
                else:
                    destination_index = len(items) - len(dragged_items)
            else:
                destination_index = group_destination_index(
                    items,
                    dragged_items,
                    self._drag_anchor_index,
                    items.index(target),
                )
            if destination_index is None:
                return
            remaining_items = move_group_to_index(
                items,
                dragged_items,
                destination_index,
            )
            remaining_items = self._order_items_by_sections(remaining_items)
            self._rebuild_items(remaining_items)
            return

        if target in dragged_items:
            return
        elif target is None:
            if items and point.Y < items[0].Bounds.Top:
                target_index = 0
            else:
                target_index = len(items)
        else:
            target_index = items.index(target)
            cursor_after = self._cursor_appears_after(
                target,
                point.Y,
                previous_boundary_y,
            )
            if should_drop_after(
                items,
                dragged_items,
                target,
                cursor_after,
            ):
                target_index += 1

        remaining_items = move_items_as_block(
            items,
            dragged_items,
            target_index,
        )
        remaining_items = self._order_items_by_sections(remaining_items)
        self._rebuild_items(remaining_items)

    def _move_selected(self, delta):
        if not self.sheet_list.SelectedItems.Count:
            return
        item = self.sheet_list.SelectedItems[0]
        group_value = self._sheet_group_value(item.Tag)
        group_items = self._group_items(group_value)
        group_index = group_items.index(item)
        new_group_index = group_index + delta
        if new_group_index < 0 or new_group_index >= len(group_items):
            return
        other_item = group_items[new_group_index]
        items = self._items()
        old_index = items.index(item)
        new_index = items.index(other_item)
        items[old_index], items[new_index] = items[new_index], items[old_index]
        items = self._order_items_by_sections(items)
        self._rebuild_items(items)
        item.EnsureVisible()

    def on_move_up(self, sender, args):
        del sender, args
        self._move_selected(-1)

    def on_move_down(self, sender, args):
        del sender, args
        self._move_selected(1)

    def on_item_checked(self, sender, args):
        del sender, args
        if self._building_list or self.IsDisposed:
            return
        self.update_preview()

    def on_setting_changed(self, sender, args):
        del sender, args
        self.update_preview()

    def _number_settings(self):
        return (
            to_text(self.prefix_box.Text),
            int(self.start_box.Value),
            int(self.step_box.Value),
            int(self.digits_box.Value),
            to_text(self.suffix_box.Text),
        )

    def _name_changes(self):
        changes = []
        for sheet in self.sheets:
            sheet_id = element_id_value(sheet.Id)
            if sheet_id not in self.sheet_name_edits:
                continue
            old_name = to_text(sheet.Name)
            new_name = to_text(self.sheet_name_edits[sheet_id]).strip()
            if new_name and new_name != old_name:
                changes.append(
                    {
                        "sheet": sheet,
                        "old": old_name,
                        "new": new_name,
                    }
                )
        return changes

    def _group_changes(self):
        changes = []
        for sheet in self.sheets:
            sheet_id = element_id_value(sheet.Id)
            if sheet_id not in self.sheet_group_edits:
                continue
            old_group = get_sheet_group_value(sheet)
            new_group = to_text(self.sheet_group_edits[sheet_id]).strip()
            if new_group and new_group != old_group:
                changes.append(
                    {
                        "sheet": sheet,
                        "old": old_group,
                        "new": new_group,
                    }
                )
        return changes

    def update_preview(self):
        if self.IsDisposed:
            return
        checked_items = self._checked_items()
        checked_ids = set(
            [element_id_value(item.Tag.Id) for item in checked_items]
        )
        occupied_by_number = {}
        for sheet in self.sheets:
            if element_id_value(sheet.Id) in checked_ids:
                continue
            occupied_by_number[to_text(sheet.SheetNumber)] = sheet
        occupied = set(occupied_by_number.keys())
        prefix, current_value, step, digits, suffix = self._number_settings()
        generated = set()
        pairs = []
        displaced_pairs = []
        errors = []

        for item in self.sheet_list.Items:
            item.SubItems[1].Text = u""

        for item in checked_items:
            candidate = format_sheet_number(
                prefix, current_value, digits, suffix
            )
            if self.skip_occupied_check.Checked:
                guard = 0
                while candidate in occupied or candidate in generated:
                    current_value += step
                    candidate = format_sheet_number(
                        prefix, current_value, digits, suffix
                    )
                    guard += 1
                    if guard > 100000:
                        errors.append(u"Не удалось найти свободный номер.")
                        break
            elif candidate in generated:
                errors.append(u"Номер {} повторяется.".format(candidate))

            item.SubItems[1].Text = candidate
            generated.add(candidate)
            pairs.append(
                {
                    "sheet": item.Tag,
                    "old": to_text(item.Tag.SheetNumber),
                    "new": candidate,
                }
            )
            current_value += step

        if not self.skip_occupied_check.Checked and not errors:
            try:
                displaced_pairs = build_priority_displacement_pairs(
                    pairs,
                    occupied_by_number,
                )
            except Exception as error:
                errors.append(to_text(error))

        displaced_by_id = dict(
            [
                (element_id_value(pair["sheet"].Id), pair["new"])
                for pair in displaced_pairs
            ]
        )
        for item in self.sheet_list.Items:
            item_id = element_id_value(item.Tag.Id)
            if item_id in displaced_by_id:
                item.SubItems[1].Text = displaced_by_id[item_id]

        self.preview_pairs = pairs
        self.preview_displaced_pairs = displaced_pairs
        self.preview_name_changes = self._name_changes()
        self.preview_group_changes = self._group_changes()
        self.preview_errors = errors
        self.selected_label.Text = (
            u"Выбрано: {}; освобождается занятых: {}; имён: {}; разделов: {}"
        ).format(
            len(pairs),
            len(displaced_pairs),
            len(self.preview_name_changes),
            len(self.preview_group_changes),
        )
        has_changes = bool(
            pairs or self.preview_name_changes or self.preview_group_changes
        )
        if errors:
            self.status_label.ForeColor = Color.DarkRed
            self.status_label.Text = errors[0]
            self.apply_button.Enabled = False
        elif pairs:
            self.status_label.ForeColor = Color.DarkGreen
            number_preview = (
                u"Предпросмотр: {} → {}  …  {} → {}".format(
                    pairs[0]["old"],
                    pairs[0]["new"],
                    pairs[-1]["old"],
                    pairs[-1]["new"],
                )
            )
            rename_count = len(self.preview_name_changes)
            section_count = len(self.preview_group_changes)
            displaced_count = len(displaced_pairs)
            if displaced_count:
                number_preview += u"; занятых номеров освобождается: {}".format(
                    displaced_count
                )
            if rename_count or section_count:
                number_preview += u"; имён: {}; разделов листов: {}".format(
                    rename_count,
                    section_count,
                )
            self.status_label.Text = number_preview
            self.apply_button.Enabled = True
        elif has_changes:
            self.status_label.ForeColor = Color.DarkGreen
            self.status_label.Text = (
                u"Подготовлено: переименований листов — {}; "
                u"изменений раздела — {}."
            ).format(
                len(self.preview_name_changes),
                len(self.preview_group_changes),
            )
            self.apply_button.Enabled = True
        else:
            self.status_label.ForeColor = Color.DimGray
            self.status_label.Text = u"Отметьте листы для перенумерации."
            self.apply_button.Enabled = False

    def _validate_changes(self, pairs, name_changes, group_changes):
        if self.doc.IsReadOnly:
            raise Exception(u"Активный документ доступен только для чтения.")
        if self.doc.IsModifiable:
            raise Exception(
                u"В документе уже открыта другая транзакция. Завершите её и повторите."
            )

        target_ids = set(
            [element_id_value(pair["sheet"].Id) for pair in pairs]
        )
        new_numbers = [pair["new"] for pair in pairs]
        if len(new_numbers) != len(set(new_numbers)):
            raise Exception(u"В предпросмотре есть повторяющиеся новые номера.")

        for sheet in get_all_sheets(self.doc):
            sheet_id = element_id_value(sheet.Id)
            if sheet_id not in target_ids and sheet.SheetNumber in new_numbers:
                raise Exception(
                    u"Номер {} уже занят листом «{}».".format(
                        sheet.SheetNumber, sheet.Name
                    )
                )

        sheet_actions = {}
        for pair in pairs:
            sheet_actions.setdefault(
                element_id_value(pair["sheet"].Id), pair["sheet"]
            )
        for change in name_changes + group_changes:
            sheet_actions.setdefault(
                element_id_value(change["sheet"].Id), change["sheet"]
            )

        for sheet_id in sheet_actions:
            sheet = sheet_actions[sheet_id]
            current_sheet = self.doc.GetElement(sheet.Id)
            if current_sheet is None or not isinstance(current_sheet, DB.ViewSheet):
                raise Exception(
                    u"Лист ID {} больше не существует.".format(
                        element_id_value(sheet.Id)
                    )
                )
            if self.doc.IsWorkshared:
                checkout_status = DB.WorksharingUtils.GetCheckoutStatus(
                    self.doc, current_sheet.Id
                )
                if to_text(checkout_status) == u"OwnedByOtherUser":
                    tooltip = DB.WorksharingUtils.GetWorksharingTooltipInfo(
                        self.doc, current_sheet.Id
                    )
                    owner = to_text(getattr(tooltip, "Owner", u"другой пользователь"))
                    raise Exception(
                        u"Лист «{}» занят пользователем {}.".format(
                            current_sheet.Name, owner
                        )
                    )

        for pair in pairs:
            current_sheet = self.doc.GetElement(pair["sheet"].Id)
            number_param = current_sheet.get_Parameter(
                DB.BuiltInParameter.SHEET_NUMBER
            )
            if number_param is None or number_param.IsReadOnly:
                raise Exception(
                    u"Номер листа «{}» недоступен для изменения.".format(
                        current_sheet.Name
                    )
                )

        for change in name_changes:
            current_sheet = self.doc.GetElement(change["sheet"].Id)
            name_param = current_sheet.get_Parameter(
                DB.BuiltInParameter.SHEET_NAME
            )
            if name_param is None or name_param.IsReadOnly:
                raise Exception(
                    u"Название листа {} недоступно для изменения.".format(
                        current_sheet.SheetNumber
                    )
                )

        for change in group_changes:
            current_sheet = self.doc.GetElement(change["sheet"].Id)
            group_param = get_sheet_group_parameter(current_sheet)
            if group_param is None:
                raise Exception(
                    u"У листа {} отсутствует параметр «{}».".format(
                        current_sheet.SheetNumber,
                        SHEET_GROUP_PARAMETER_NAMES[0],
                    )
                )
            if group_param.IsReadOnly:
                raise Exception(
                    u"Раздел листа {} недоступен для изменения.".format(
                        current_sheet.SheetNumber
                    )
                )

    def _apply_changes(self, pairs, name_changes, group_changes):
        self._validate_changes(pairs, name_changes, group_changes)
        transaction = DB.Transaction(
            self.doc, "Renumber and rename sheets"
        )
        transaction.Start()
        try:
            token = Guid.NewGuid().ToString("N")
            for index, pair in enumerate(pairs):
                pair["sheet"].SheetNumber = (
                    u"__PYREVIT_RENUMBER_{}_{}_{}__".format(
                        token,
                        element_id_value(pair["sheet"].Id),
                        index,
                    )
                )
            for pair in pairs:
                pair["sheet"].SheetNumber = pair["new"]
            for change in name_changes:
                current_sheet = self.doc.GetElement(change["sheet"].Id)
                name_param = current_sheet.get_Parameter(
                    DB.BuiltInParameter.SHEET_NAME
                )
                if not name_param.Set(change["new"]):
                    raise Exception(
                        u"Не удалось переименовать лист {}.".format(
                            current_sheet.SheetNumber
                        )
                    )
            for change in group_changes:
                current_sheet = self.doc.GetElement(change["sheet"].Id)
                group_param = get_sheet_group_parameter(current_sheet)
                if not group_param.Set(change["new"]):
                    raise Exception(
                        u"Не удалось изменить раздел листа {}.".format(
                            current_sheet.SheetNumber
                        )
                    )
            result = transaction.Commit()
            if result != DB.TransactionStatus.Committed:
                raise Exception(
                    u"Revit не подтвердил транзакцию: {}".format(result)
                )
        except Exception:
            if transaction.GetStatus() == DB.TransactionStatus.Started:
                transaction.RollBack()
            raise

    def on_apply(self, sender, args):
        del sender, args
        self.update_preview()
        if self.preview_errors:
            return

        selected_pairs = list(self.preview_pairs)
        displaced_pairs = list(self.preview_displaced_pairs)
        pairs = selected_pairs + displaced_pairs
        name_changes = list(self.preview_name_changes)
        group_changes = list(self.preview_group_changes)
        if not pairs and not name_changes and not group_changes:
            return

        message_lines = []
        if selected_pairs:
            message_lines.extend(
                [
                    u"Будет перенумеровано выбранных листов: {}".format(
                        len(selected_pairs)
                    ),
                    u"Первый: {} → {}".format(
                        selected_pairs[0]["old"], selected_pairs[0]["new"]
                    ),
                    u"Последний: {} → {}".format(
                        selected_pairs[-1]["old"], selected_pairs[-1]["new"]
                    ),
                ]
            )
        if displaced_pairs:
            message_lines.append(
                u"Невыбранных листов получат освобождённые номера: {}".format(
                    len(displaced_pairs)
                )
            )
        if name_changes:
            message_lines.append(
                u"Будет переименовано листов: {}".format(len(name_changes))
            )
        if group_changes:
            message_lines.append(
                u"Будет изменён раздел у листов: {}".format(
                    len(group_changes)
                )
            )
        message_lines.extend(
            [
                u"",
                u"Все изменения будут одной операцией Undo. Продолжить?",
            ]
        )
        message = u"\n".join(message_lines)
        answer = MessageBox.Show(
            message,
            WINDOW_TITLE,
            MessageBoxButtons.YesNo,
            MessageBoxIcon.Question,
        )
        if answer != DialogResult.Yes:
            return

        try:
            self._apply_changes(pairs, name_changes, group_changes)
        except Exception as error:
            forms.alert(
                u"Изменения отменены. Модель не изменена.\n\n{}".format(
                    error
                ),
                title=WINDOW_TITLE,
                warn_icon=True,
            )
            return

        print(u"Sheet renumbering and renaming")
        print(u"Renumbered selected: {}".format(len(selected_pairs)))
        print(u"Renumbered conflicting unselected: {}".format(len(displaced_pairs)))
        for pair in pairs:
            print(
                u"{} -> {} | {} | ID {}".format(
                    pair["old"],
                    pair["new"],
                    pair["sheet"].Name,
                    element_id_value(pair["sheet"].Id),
                )
            )
        print(u"Renamed sheets: {}".format(len(name_changes)))
        print(u"Changed sheet sections: {}".format(len(group_changes)))

        self.applied_count = len(pairs) + len(name_changes) + len(group_changes)
        forms.alert(
            (
                u"Готово. Выбранных перенумеровано: {}; освобождено занятых: {}; "
                u"переименовано: {}; "
                u"изменён раздел: {}.\n"
                u"Документ не сохранён и не синхронизирован автоматически."
            ).format(
                len(selected_pairs),
                len(displaced_pairs),
                len(name_changes),
                len(group_changes),
            ),
            title=WINDOW_TITLE,
        )
        self.DialogResult = DialogResult.OK
        self.Close()


def main():
    doc = revit.doc
    if doc is None or doc.IsFamilyDocument:
        forms.alert(
            u"Откройте проект Revit с листами.",
            title=WINDOW_TITLE,
            warn_icon=True,
        )
        return

    sheets = get_all_sheets(doc)
    if not sheets:
        forms.alert(
            u"В активном проекте нет листов.",
            title=WINDOW_TITLE,
            warn_icon=True,
        )
        return

    selected_ids = []
    try:
        selected_sheets = revit.get_selection().include(DB.ViewSheet).elements
        selected_ids = [element_id_value(sheet.Id) for sheet in selected_sheets]
    except Exception:
        selected_ids = []

    dialog = RenumberSheetsForm(doc, sheets, selected_ids)
    dialog.ShowDialog()


if __name__ == "__main__":
    main()
