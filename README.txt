для роботи створи файл config.py:
    BOT_TOKEN = "YOUR:Token"

    MOD_CHAT_ID = -100000000000
    CHANNEL_ID = -100000000000
source venv/Scripts/activate - активувати віртуальне середовище (для бібліотек)
rm bot.db - видалити бд
python checker.py - запуск скрипта
python moderator_bot.py - запуск модерації (має працювати завжди)
python build_games_csv.py - створити список ігор
todo:
виводити blacklist з можливістю прибрати звідти гру