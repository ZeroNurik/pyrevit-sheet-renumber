# -*- coding: utf-8 -*-
"""Flexible and safe batch renumbering for Revit sheets."""

import re

import clr

clr.AddReference("System.Drawing")
clr.AddReference("System.Windows.Forms")

from System import Action, Guid
from System.Drawing import Color, Point, Size
from System.Windows.Forms import (
    AnchorStyles,
    Button,
    CheckBox,
    Cursor,
    DialogResult,
    DragDropEffects,
    Form,
    FormBorderStyle,
    FormStartPosition,
    HorizontalAlignment,
    Label,
    ListView,
    ListViewItem,
    MessageBox,
    MessageBoxButtons,
    MessageBoxIcon,
    MouseButtons,
    NumericUpDown,
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


def format_sheet_number(prefix, value, digits, suffix):
    return u"{}{}{}".format(prefix, to_text(value).zfill(digits), suffix)


def sort_items_by_groups(items, groups, reverse_names=False):
    """Sort items by the first matching name filter, then by sheet name."""
    buckets = [[] for group in groups]
    unmatched = []

    for item in items:
        sheet_name = to_text(item.Tag.Name).lower()
        matched_index = None
        for index, group in enumerate(groups):
            if group[1].lower() in sheet_name:
                matched_index = index
                break
        if matched_index is None:
            unmatched.append(item)
        else:
            buckets[matched_index].append(item)

    sort_key = lambda item: (
        natural_key(item.Tag.Name),
        natural_key(item.Tag.SheetNumber),
    )
    ordered = []
    for bucket in buckets:
        bucket.sort(key=sort_key, reverse=reverse_names)
        ordered.extend(bucket)
    unmatched.sort(key=sort_key, reverse=reverse_names)
    ordered.extend(unmatched)
    return ordered


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
    """Collect up to five user-defined sheet-name filters."""

    MAX_GROUPS = 5

    def __init__(self, groups):
        Form.__init__(self)
        self.groups = []
        self.group_boxes = []
        self.filter_boxes = []

        self.Text = u"Настройка автосортировки"
        self.StartPosition = FormStartPosition.CenterParent
        self.FormBorderStyle = FormBorderStyle.FixedDialog
        self.MinimizeBox = False
        self.MaximizeBox = False
        self.ShowInTaskbar = False
        self.ClientSize = Size(760, 390)

        description = Label()
        description.Text = (
            u"Введите до 5 групп. Листы проверяются сверху вниз; "
            u"пустые фильтры не используются."
        )
        description.Location = Point(12, 12)
        description.Size = Size(730, 38)
        self.Controls.Add(description)

        group_header = Label()
        group_header.Text = u"Название группы"
        group_header.Location = Point(48, 55)
        group_header.AutoSize = True
        self.Controls.Add(group_header)

        filter_header = Label()
        filter_header.Text = u"Текст, содержащийся в названии листа"
        filter_header.Location = Point(290, 55)
        filter_header.AutoSize = True
        self.Controls.Add(filter_header)

        for index in range(self.MAX_GROUPS):
            row_y = 82 + index * 43

            number_label = Label()
            number_label.Text = to_text(index + 1)
            number_label.Location = Point(16, row_y + 4)
            number_label.Size = Size(25, 25)
            self.Controls.Add(number_label)

            group_box = TextBox()
            group_box.Location = Point(48, row_y)
            group_box.Size = Size(220, 25)
            self.Controls.Add(group_box)
            self.group_boxes.append(group_box)

            filter_box = TextBox()
            filter_box.Location = Point(290, row_y)
            filter_box.Size = Size(452, 25)
            self.Controls.Add(filter_box)
            self.filter_boxes.append(filter_box)

            if index < len(groups):
                group_box.Text = groups[index][0]
                filter_box.Text = groups[index][1]

        note = Label()
        note.Text = (
            u"Совпадение выполняется без учёта регистра. "
            u"Несовпавшие листы останутся в конце."
        )
        note.Location = Point(12, 305)
        note.Size = Size(570, 35)
        self.Controls.Add(note)

        apply_button = Button()
        apply_button.Text = u"Применить сортировку"
        apply_button.Location = Point(560, 342)
        apply_button.Size = Size(182, 34)
        apply_button.Click += self.on_apply
        self.Controls.Add(apply_button)
        self.AcceptButton = apply_button

        cancel_button = Button()
        cancel_button.Text = u"Отмена"
        cancel_button.Location = Point(444, 342)
        cancel_button.Size = Size(106, 34)
        cancel_button.DialogResult = DialogResult.Cancel
        self.Controls.Add(cancel_button)
        self.CancelButton = cancel_button

    def on_apply(self, sender, args):
        del sender, args
        groups = []
        for index in range(self.MAX_GROUPS):
            filter_text = to_text(self.filter_boxes[index].Text).strip()
            if not filter_text:
                continue
            group_name = to_text(self.group_boxes[index].Text).strip()
            if not group_name:
                group_name = u"Группа {}".format(index + 1)
            groups.append((group_name, filter_text))

        if not groups:
            MessageBox.Show(
                u"Введите текст фильтра хотя бы для одной группы.",
                u"Автосортировка",
                MessageBoxButtons.OK,
                MessageBoxIcon.Warning,
            )
            return

        self.groups = groups
        self.DialogResult = DialogResult.OK
        self.Close()


class RenumberSheetsForm(Form):
    def __init__(self, doc, sheets, preselected_ids):
        Form.__init__(self)
        self.doc = doc
        self.sheets = sheets
        self.preselected_ids = set(preselected_ids)
        self.preview_pairs = []
        self.preview_errors = []
        self.applied_count = 0
        self._building_list = False
        self._dragged_items = []
        self._drag_anchor_index = None
        self._drag_scroll_direction = 0
        self._drag_scroll_timer = Timer()
        self._drag_scroll_timer.Interval = 100
        self._drag_scroll_timer.Tick += self.on_drag_scroll_tick
        self.sort_groups = []

        self.Text = WINDOW_TITLE
        self.StartPosition = FormStartPosition.CenterScreen
        self.FormBorderStyle = FormBorderStyle.Sizable
        self.MinimumSize = Size(900, 620)
        self.Size = Size(1120, 740)

        self._build_controls()
        self.FormClosed += self.on_form_closed
        self._load_sheets()
        self.update_preview()

    def _build_controls(self):
        self.sort_label = Label()
        self.sort_label.Text = u"Автосортировка по названию листа:"
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
        self.sort_status_label.Text = u"Группы не настроены"
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
        self.sheet_list.Columns.Add(u"Текущий", 115, HorizontalAlignment.Left)
        self.sheet_list.Columns.Add(u"Новый", 115, HorizontalAlignment.Left)
        self.sheet_list.Columns.Add(u"Название листа", 810, HorizontalAlignment.Left)
        self.sheet_list.ItemChecked += self.on_item_checked
        self.sheet_list.ItemDrag += self.on_item_drag
        self.sheet_list.DragEnter += self.on_drag_enter
        self.sheet_list.DragOver += self.on_drag_over
        self.sheet_list.DragLeave += self.on_drag_leave
        self.sheet_list.DragDrop += self.on_drag_drop
        self.Controls.Add(self.sheet_list)

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
        self.skip_occupied_check.Text = u"Пропускать номера, занятые невыбранными листами"
        self.skip_occupied_check.Location = Point(770, input_y + 1)
        self.skip_occupied_check.AutoSize = True
        self.skip_occupied_check.Checked = True
        self.skip_occupied_check.Anchor = AnchorStyles.Bottom | AnchorStyles.Left
        self.skip_occupied_check.CheckedChanged += self.on_setting_changed
        self.Controls.Add(self.skip_occupied_check)

        self.status_label = Label()
        self.status_label.Text = u""
        self.status_label.Location = Point(12, 610)
        self.status_label.Size = Size(790, 45)
        self.status_label.Anchor = (
            AnchorStyles.Bottom | AnchorStyles.Left | AnchorStyles.Right
        )
        self.Controls.Add(self.status_label)

        self.apply_button = Button()
        self.apply_button.Text = u"Применить"
        self.apply_button.Location = Point(862, 625)
        self.apply_button.Size = Size(110, 34)
        self.apply_button.Anchor = AnchorStyles.Bottom | AnchorStyles.Right
        self.apply_button.Click += self.on_apply
        self.Controls.Add(self.apply_button)

        self.cancel_button = Button()
        self.cancel_button.Text = u"Отмена"
        self.cancel_button.Location = Point(982, 625)
        self.cancel_button.Size = Size(110, 34)
        self.cancel_button.Anchor = AnchorStyles.Bottom | AnchorStyles.Right
        self.cancel_button.DialogResult = DialogResult.Cancel
        self.Controls.Add(self.cancel_button)
        self.CancelButton = self.cancel_button

    def _load_sheets(self):
        ordered = sorted(
            self.sheets,
            key=lambda sheet: (
                natural_key(sheet.SheetNumber),
                natural_key(sheet.Name),
            ),
        )
        self._building_list = True
        try:
            self.sheet_list.Items.Clear()
            for sheet in ordered:
                current_number = to_text(sheet.SheetNumber)
                sheet_name = to_text(sheet.Name)
                if getattr(sheet, "IsPlaceholder", False):
                    sheet_name = u"{} [заполнитель]".format(sheet_name)
                item = ListViewItem(current_number)
                item.SubItems.Add(u"")
                item.SubItems.Add(sheet_name)
                item.Tag = sheet
                item.Checked = element_id_value(sheet.Id) in self.preselected_ids
                self.sheet_list.Items.Add(item)
        finally:
            self._building_list = False

    def _items(self):
        return [item for item in self.sheet_list.Items]

    def _checked_items(self):
        return [item for item in self.sheet_list.Items if item.Checked]

    def _rebuild_items(self, items):
        top_index = 0
        try:
            if self.sheet_list.TopItem is not None:
                top_index = self.sheet_list.TopItem.Index
        except Exception:
            top_index = 0

        selected_ids = set(
            element_id_value(item.Tag.Id)
            for item in self.sheet_list.SelectedItems
        )
        focused_set = False
        self._building_list = True
        try:
            self.sheet_list.Items.Clear()
            for item in items:
                self.sheet_list.Items.Add(item)
                if element_id_value(item.Tag.Id) in selected_ids:
                    item.Selected = True
                    if not focused_set:
                        item.Focused = True
                        focused_set = True
        finally:
            self._building_list = False

        if self.sheet_list.Items.Count:
            restored_index = min(
                top_index,
                self.sheet_list.Items.Count - 1,
            )
            try:
                self.sheet_list.TopItem = self.sheet_list.Items[restored_index]
            except Exception:
                self.sheet_list.Items[restored_index].EnsureVisible()
        self.update_preview()

    def on_configure_sort(self, sender, args):
        del sender, args
        settings_form = AutoSortSettingsForm(self.sort_groups)
        try:
            if settings_form.ShowDialog(self) != DialogResult.OK:
                return
            self.sort_groups = list(settings_form.groups)
        finally:
            settings_form.Dispose()

        ordered = sort_items_by_groups(
            self._items(),
            self.sort_groups,
            self.reverse_check.Checked,
        )

        names = [group[0] for group in self.sort_groups]
        self.sort_status_label.Text = u"Группы: {}".format(u" → ".join(names))
        self._rebuild_items(ordered)

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
            self.sheet_list.InsertionMark.Index = -1

    def on_drag_enter(self, sender, args):
        del sender
        if args.Data.GetDataPresent(ListViewItem):
            args.Effect = DragDropEffects.Move
        else:
            args.Effect = getattr(DragDropEffects, "None")

    def _update_drag_marker(self, point):
        target = self.sheet_list.GetItemAt(point.X, point.Y)
        items = self._items()
        dragged_items = [
            item for item in self._dragged_items if item in items
        ]

        if len(dragged_items) > 1:
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
                self.sheet_list.InsertionMark.Index = -1
            else:
                self.sheet_list.InsertionMark.Index = destination_index
                self.sheet_list.InsertionMark.AppearsAfterItem = False
            return

        if target is None:
            if items:
                first_item = items[0]
                if point.Y < first_item.Bounds.Top:
                    self.sheet_list.InsertionMark.Index = 0
                    self.sheet_list.InsertionMark.AppearsAfterItem = False
                else:
                    self.sheet_list.InsertionMark.Index = len(items) - 1
                    self.sheet_list.InsertionMark.AppearsAfterItem = True
            return

        if target in dragged_items:
            self.sheet_list.InsertionMark.Index = -1
            return

        cursor_after = (
            point.Y > target.Bounds.Top + target.Bounds.Height / 2
        )
        self.sheet_list.InsertionMark.Index = target.Index
        self.sheet_list.InsertionMark.AppearsAfterItem = should_drop_after(
            items,
            dragged_items,
            target,
            cursor_after,
        )

    def _stop_drag_scroll(self):
        self._drag_scroll_direction = 0
        if self._drag_scroll_timer.Enabled:
            self._drag_scroll_timer.Stop()

    def _update_drag_scroll(self, point):
        if not self.sheet_list.Items.Count:
            self._stop_drag_scroll()
            return

        content_top = 0
        try:
            if self.sheet_list.TopItem is not None:
                content_top = self.sheet_list.TopItem.Bounds.Top
        except Exception:
            content_top = 0

        threshold = 48
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

        if distance <= 14:
            interval = 45
        elif distance <= 30:
            interval = 75
        else:
            interval = 110

        self._drag_scroll_direction = direction
        self._drag_scroll_timer.Interval = interval
        if not self._drag_scroll_timer.Enabled:
            self._drag_scroll_timer.Start()

    def on_drag_scroll_tick(self, sender, args):
        del sender, args
        if not self._dragged_items or self._drag_scroll_direction == 0:
            self._stop_drag_scroll()
            return

        try:
            top_item = self.sheet_list.TopItem
            if top_item is None:
                self._stop_drag_scroll()
                return

            old_index = top_item.Index
            new_index = old_index + self._drag_scroll_direction
            new_index = max(
                0,
                min(self.sheet_list.Items.Count - 1, new_index),
            )
            if new_index == old_index:
                self._stop_drag_scroll()
                return

            self.sheet_list.TopItem = self.sheet_list.Items[new_index]
            if self.sheet_list.TopItem.Index == old_index:
                self._stop_drag_scroll()
                return

            point = self.sheet_list.PointToClient(Cursor.Position)
            if self.sheet_list.ClientRectangle.Contains(point):
                self._update_drag_marker(point)
        except Exception:
            self._stop_drag_scroll()

    def on_drag_over(self, sender, args):
        del sender
        if not args.Data.GetDataPresent(ListViewItem):
            args.Effect = getattr(DragDropEffects, "None")
            self._stop_drag_scroll()
            self.sheet_list.InsertionMark.Index = -1
            return

        args.Effect = DragDropEffects.Move
        point = self.sheet_list.PointToClient(Point(args.X, args.Y))
        self._update_drag_scroll(point)
        self._update_drag_marker(point)

    def on_drag_leave(self, sender, args):
        del sender, args
        self._stop_drag_scroll()
        self.sheet_list.InsertionMark.Index = -1

    def on_form_closed(self, sender, args):
        del sender, args
        self._stop_drag_scroll()
        self._drag_scroll_timer.Dispose()

    def on_drag_drop(self, sender, args):
        del sender
        self._stop_drag_scroll()
        self.sheet_list.InsertionMark.Index = -1
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
        target = self.sheet_list.GetItemAt(point.X, point.Y)

        if len(dragged_items) > 1:
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
            if destination_index is None:
                return
            remaining_items = move_group_to_index(
                items,
                dragged_items,
                destination_index,
            )
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
            cursor_after = (
                point.Y > target.Bounds.Top + target.Bounds.Height / 2
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
        self._rebuild_items(remaining_items)

    def _move_selected(self, delta):
        if not self.sheet_list.SelectedItems.Count:
            return
        item = self.sheet_list.SelectedItems[0]
        old_index = item.Index
        new_index = old_index + delta
        if new_index < 0 or new_index >= self.sheet_list.Items.Count:
            return
        items = self._items()
        items.pop(old_index)
        items.insert(new_index, item)
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
        if not self._building_list:
            self.BeginInvoke(Action(self.update_preview))

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

    def update_preview(self):
        if self.IsDisposed:
            return
        checked_items = self._checked_items()
        checked_ids = set(
            [element_id_value(item.Tag.Id) for item in checked_items]
        )
        occupied = set(
            [
                to_text(sheet.SheetNumber)
                for sheet in self.sheets
                if element_id_value(sheet.Id) not in checked_ids
            ]
        )
        prefix, current_value, step, digits, suffix = self._number_settings()
        generated = set()
        pairs = []
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
            elif candidate in occupied:
                errors.append(
                    u"Номер {} занят невыбранным листом.".format(candidate)
                )
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

        self.preview_pairs = pairs
        self.preview_errors = errors
        self.selected_label.Text = u"Выбрано: {}".format(len(pairs))
        if errors:
            self.status_label.ForeColor = Color.DarkRed
            self.status_label.Text = errors[0]
            self.apply_button.Enabled = False
        elif pairs:
            self.status_label.ForeColor = Color.DarkGreen
            self.status_label.Text = (
                u"Предпросмотр: {} → {}  …  {} → {}".format(
                    pairs[0]["old"],
                    pairs[0]["new"],
                    pairs[-1]["old"],
                    pairs[-1]["new"],
                )
            )
            self.apply_button.Enabled = True
        else:
            self.status_label.ForeColor = Color.DimGray
            self.status_label.Text = u"Отметьте листы для перенумерации."
            self.apply_button.Enabled = False

    def _validate_pairs(self, pairs):
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

        for pair in pairs:
            sheet = pair["sheet"]
            current_sheet = self.doc.GetElement(sheet.Id)
            if current_sheet is None or not isinstance(current_sheet, DB.ViewSheet):
                raise Exception(
                    u"Лист ID {} больше не существует.".format(
                        element_id_value(sheet.Id)
                    )
                )
            number_param = current_sheet.get_Parameter(
                DB.BuiltInParameter.SHEET_NUMBER
            )
            if number_param is None or number_param.IsReadOnly:
                raise Exception(
                    u"Номер листа «{}» недоступен для изменения.".format(
                        current_sheet.Name
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

    def _apply_pairs(self, pairs):
        self._validate_pairs(pairs)
        transaction = DB.Transaction(
            self.doc, "Flexible sheet renumbering"
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
        if self.preview_errors or not self.preview_pairs:
            return

        pairs = list(self.preview_pairs)
        message = (
            u"Будет перенумеровано листов: {}\n\n"
            u"Первый: {} → {}\n"
            u"Последний: {} → {}\n\n"
            u"Изменение будет одной операцией Undo. Продолжить?"
        ).format(
            len(pairs),
            pairs[0]["old"],
            pairs[0]["new"],
            pairs[-1]["old"],
            pairs[-1]["new"],
        )
        answer = MessageBox.Show(
            message,
            WINDOW_TITLE,
            MessageBoxButtons.YesNo,
            MessageBoxIcon.Question,
        )
        if answer != DialogResult.Yes:
            return

        try:
            self._apply_pairs(pairs)
        except Exception as error:
            forms.alert(
                u"Перенумерация отменена. Модель не изменена.\n\n{}".format(
                    error
                ),
                title=WINDOW_TITLE,
                warn_icon=True,
            )
            return

        print(u"Flexible sheet renumbering")
        print(u"Renumbered: {}".format(len(pairs)))
        for pair in pairs:
            print(
                u"{} -> {} | {} | ID {}".format(
                    pair["old"],
                    pair["new"],
                    pair["sheet"].Name,
                    element_id_value(pair["sheet"].Id),
                )
            )

        self.applied_count = len(pairs)
        forms.alert(
            u"Готово. Перенумеровано листов: {}.\n"
            u"Документ не сохранён и не синхронизирован автоматически.".format(
                len(pairs)
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
