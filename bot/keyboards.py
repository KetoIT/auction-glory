from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from db.models import Auction


def main_nav_kb(is_admin: bool = False) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(text="🖼 Актуальные картины", callback_data="nav:live")],
        [
            InlineKeyboardButton(text="🎨 Галерея", callback_data="nav:gallery"),
            InlineKeyboardButton(text="✉️ Под заказ", callback_data="nav:order"),
        ],
        [InlineKeyboardButton(text="📜 Правила", callback_data="nav:rules")],
    ]
    if is_admin:
        rows.append([InlineKeyboardButton(text="🛠 Админ", callback_data="nav:admin")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def rules_back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🏠 В главное меню", callback_data="nav:home")]]
    )


def main_reply_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🖼 Актуальные картины")],
            [KeyboardButton(text="🎨 Галерея"), KeyboardButton(text="✉️ Под заказ")],
        ],
        resize_keyboard=True,
    )


def auction_card_kb(auction_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="История ставок", callback_data=f"a:{auction_id}:b"),
                InlineKeyboardButton(text="Ставка", callback_data=f"a:{auction_id}:p"),
            ],
            [InlineKeyboardButton(text="К лотам", callback_data="nav:live")],
        ]
    )


def bid_presets_kb(auction_id: int, step: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    presets = [100, 500, 1000]
    row: list[InlineKeyboardButton] = []
    for p in presets:
        if p >= step:
            row.append(
                InlineKeyboardButton(
                    text=f"+{p} ₽",
                    callback_data=f"b:{auction_id}:{p}",
                )
            )
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="Своя сумма", callback_data=f"b:{auction_id}:x")])
    rows.append([InlineKeyboardButton(text="К карточке", callback_data=f"a:{auction_id}:v")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def gallery_back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="К галерее", callback_data="nav:gallery")]]
    )


def order_kb(contact_url: str) -> InlineKeyboardMarkup:
    url = contact_url.strip()
    if not url.startswith("http"):
        url = f"https://t.me/{url.lstrip('@')}"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💬 Написать художнику", url=url)],
            [InlineKeyboardButton(text="🏠 В главное меню", callback_data="nav:home")],
        ]
    )


def admin_main_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="➕ Новый лот", callback_data="adm:new"),
                InlineKeyboardButton(text="📋 Активные лоты", callback_data="adm:list"),
            ],
            [
                InlineKeyboardButton(text="📣 Рассылка", callback_data="adm:broadcast"),
                InlineKeyboardButton(text="✏️ Изменить лот", callback_data="adm:edit"),
            ],
            [InlineKeyboardButton(text="🏠 В главное меню", callback_data="nav:home")],
        ]
    )


def admin_broadcast_confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Отправить всем", callback_data="adm:bc_send"),
                InlineKeyboardButton(text="✖️ Отмена", callback_data="adm:bc_cancel"),
            ],
            [InlineKeyboardButton(text="🏠 В главное меню", callback_data="nav:home")],
        ]
    )


def admin_edit_list_kb(auctions: list[Auction]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for a in auctions:
        short = (a.title[:28] + "…") if len(a.title) > 28 else a.title
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"#{a.id} · {short}",
                    callback_data=f"adm:esel:{a.id}",
                ),
            ]
        )
    rows.append([InlineKeyboardButton(text="⬅️ В админ-меню", callback_data="adm:home")])
    rows.append([InlineKeyboardButton(text="🏠 В главное меню", callback_data="nav:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_edit_field_kb(auction_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📝 Название", callback_data=f"adm:ef:{auction_id}:title")],
            [InlineKeyboardButton(text="📄 Описание", callback_data=f"adm:ef:{auction_id}:desc")],
            [InlineKeyboardButton(text="🖼 Фото", callback_data=f"adm:ef:{auction_id}:photo")],
            [
                InlineKeyboardButton(text="💰 Цены (старт и шаг)", callback_data=f"adm:ef:{auction_id}:prices"),
            ],
            [
                InlineKeyboardButton(text="🕐 Дата старта", callback_data=f"adm:ef:{auction_id}:start"),
                InlineKeyboardButton(text="🕠 Дата конца", callback_data=f"adm:ef:{auction_id}:end"),
            ],
            [
                InlineKeyboardButton(
                    text="🖼 Показ в галерее (вкл/выкл)",
                    callback_data=f"adm:ef:{auction_id}:toggle",
                ),
            ],
            [InlineKeyboardButton(text="⬅️ К списку лотов", callback_data="adm:edit")],
            [InlineKeyboardButton(text="🏠 В главное меню", callback_data="nav:home")],
        ]
    )


def admin_auctions_manage_kb(auctions: list[Auction]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for a in auctions:
        short = (a.title[:20] + "…") if len(a.title) > 20 else a.title
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🏁 Завершить #{a.id} · {short}",
                    callback_data=f"adm:eq:{a.id}",
                ),
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"🗑 Отменить #{a.id}",
                    callback_data=f"adm:cq:{a.id}",
                ),
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"📊 #{a.id} · статистика",
                    callback_data=f"adm:stat:{a.id}",
                ),
                InlineKeyboardButton(
                    text="⏱ Продлить",
                    callback_data=f"adm:emenu:{a.id}",
                ),
            ]
        )
    rows.append([InlineKeyboardButton(text="🔄 Обновить список", callback_data="adm:list")])
    rows.append([InlineKeyboardButton(text="⬅️ В админ-меню", callback_data="adm:home")])
    rows.append([InlineKeyboardButton(text="🏠 В главное меню", callback_data="nav:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_confirm_end_kb(aid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Да, завершить", callback_data=f"adm:ed:{aid}"),
                InlineKeyboardButton(text="↩️ Нет", callback_data="adm:list"),
            ],
            [InlineKeyboardButton(text="🏠 В главное меню", callback_data="nav:home")],
        ]
    )


def admin_confirm_cancel_kb(aid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Да, отменить", callback_data=f"adm:cd:{aid}"),
                InlineKeyboardButton(text="↩️ Нет", callback_data="adm:list"),
            ],
            [InlineKeyboardButton(text="🏠 В главное меню", callback_data="nav:home")],
        ]
    )


def admin_confirm_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🚀 Опубликовать", callback_data="adm:pub"),
                InlineKeyboardButton(text="✖️ Отмена", callback_data="adm:cancel"),
            ],
            [InlineKeyboardButton(text="🏠 В главное меню", callback_data="nav:home")],
        ]
    )


def admin_extend_time_kb(aid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="+15 мин", callback_data=f"adm:ext:{aid}:15"),
                InlineKeyboardButton(text="+30 мин", callback_data=f"adm:ext:{aid}:30"),
            ],
            [
                InlineKeyboardButton(text="+1 ч", callback_data=f"adm:ext:{aid}:60"),
                InlineKeyboardButton(text="+3 ч", callback_data=f"adm:ext:{aid}:180"),
            ],
            [InlineKeyboardButton(text="+24 ч", callback_data=f"adm:ext:{aid}:1440")],
            [
                InlineKeyboardButton(text="⬅️ К списку лотов", callback_data="adm:list"),
                InlineKeyboardButton(text="🏠 В главное меню", callback_data="nav:home"),
            ],
        ]
    )


def admin_stat_back_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="⬅️ К списку лотов", callback_data="adm:list"),
                InlineKeyboardButton(text="🏠 В главное меню", callback_data="nav:home"),
            ],
        ]
    )
