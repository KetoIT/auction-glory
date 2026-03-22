from aiogram.fsm.state import State, StatesGroup


class CustomBidStates(StatesGroup):
    entering_amount = State()


class NewAuctionStates(StatesGroup):
    photo = State()
    title = State()
    description = State()
    start_price = State()
    step_amount = State()
    starts_at = State()
    ends_at = State()
    confirm = State()


class BroadcastStates(StatesGroup):
    entering = State()
    confirm = State()


class EditAuctionStates(StatesGroup):
    waiting_value = State()
