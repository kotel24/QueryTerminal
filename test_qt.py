from qt import QT

def test_exec_sql_basic_flow(capsys):
    qt = QT(":memory:")
    qt._exec_sql("CREATE TABLE users(id INTEGER, name TEXT);")
    qt._exec_sql("INSERT INTO users VALUES (1, 'Alice'), (2, 'Bob');")

    capsys.readouterr()  # очистить буфер перед SELECT
    qt._exec_sql("SELECT * FROM users;")

    out = capsys.readouterr().out
    assert "Alice" in out
    assert "Bob" in out
    assert "OK" not in out  # SELECT не должен печатать OK
    assert "id" in out and "name" in out

def test_exec_sql_error_and_rollback(capsys):
    qt = QT(":memory:")
    qt._exec_sql("CREATE TABLE items(id INTEGER UNIQUE);")
    qt._exec_sql("INSERT INTO items VALUES (1);")

    # Ошибочный SQL
    qt._exec_sql("INSER INTO items VALUES (2);")  # опечатка
    out = capsys.readouterr().out
    assert "SQL error" in out

    # Проверяем, что в таблице осталась только первая запись
    cur = qt.rt.conn.execute("SELECT COUNT(*) FROM items;")
    assert cur.fetchone()[0] == 1

def test_exec_sql_with_timer_output(capsys):
    qt = QT(":memory:")
    qt._timer = True
    qt._exec_sql("CREATE TABLE test(id INTEGER);")
    qt._exec_sql("INSERT INTO test VALUES (1);")
    qt._exec_sql("SELECT * FROM test;")
    out = capsys.readouterr().out
    assert "(Time:" in out  # должен появиться таймер
    assert "OK" in out or "id" in out  # SQL отработал корректно

def test_exec_sql_empty_result(capsys):
    qt = QT(":memory:")
    qt._exec_sql("CREATE TABLE data(x TEXT);")
    capsys.readouterr()  # очистить вывод
    qt._exec_sql("SELECT * FROM data;")

    out = capsys.readouterr().out
    assert "(empty)" in out
    assert "x" in out  # заголовок присутствует


def test_exec_sql_with_null_and_types(capsys):
    qt = QT(":memory:")
    qt._exec_sql("CREATE TABLE vals(id INTEGER, val REAL, txt TEXT);")
    qt._exec_sql("INSERT INTO vals VALUES (1, 3.14, 'pi');")
    qt._exec_sql("INSERT INTO vals VALUES (2, NULL, NULL);")

    capsys.readouterr()
    qt._exec_sql("SELECT * FROM vals ORDER BY id;")

    out = capsys.readouterr().out
    assert "pi" in out
    assert "3.14" in out
    assert "(empty)" not in out
    # NULL-поля отображаются как пустые
    assert "2" in out  # вторая строка присутствует