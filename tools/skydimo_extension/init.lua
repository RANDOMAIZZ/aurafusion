-- AuraFusion bridge extension (v3).
--
-- Протокол с AuraFusion (tcp 127.0.0.1:8766), двоичный:
--   1. расширение после подключения шлёт 2 байта: число светодиодов (LE);
--   2. дальше сообщения: 1 байт типа + нагрузка:
--        'F' + leds*3 байт (R,G,B) — красим светодиоды сами (ext.set_leds);
--        'B' + 1 байт 0..100       — цвет по экрану считает сама Skydimo
--                                    (эффект ScreenMirror), мы меняем яркость.
-- Раньше кадры были текстом и читались по одному байту (десятки тысяч
-- вызовов в секунду), а в ext.set_leds уходили таблицы {r,g,b} — API ждёт
-- кортежи {индекс, r, g, b}, и светодиоды на деле не красились.
-- Чтобы эффект Skydimo (ScreenMirror и т.п.) не перекрывал наши кадры,
-- светодиоды берутся под блокировку через ext.lock_leds.

local tcp = ext.net.tcp

local P = {}

local HOST = "127.0.0.1"
local PORT = 8766

local dbg_path = nil
local function dlog(msg)
    pcall(ext.log, "aurafusion: " .. tostring(msg))
    -- ext.log снаружи не видно, поэтому дублируем в файл
    pcall(function()
        dbg_path = dbg_path or (tostring(ext.data_dir) .. "/aurafusion_ext.log")
        local f = io.open(dbg_path, "a")
        if f then
            f:write(os.date("%H:%M:%S ") .. tostring(msg) .. "\n")
            f:close()
        end
    end)
end

local function find_target()
    local ok, devices = pcall(ext.get_devices)
    if not ok or type(devices) ~= "table" then
        return nil, nil, 0, nil
    end
    for _, dev in pairs(devices) do
        local port = tostring(dev.port or "")
        local path = tostring(dev.device_path or "")
        local cid = tostring(dev.controller_id or "")
        local name = tostring(dev.name or dev.nickname or "")
        local out_id, leds = nil, 0
        if type(dev.outputs) == "table" then
            local o = dev.outputs[1]
            if type(o) == "table" then
                out_id = o.id or o.outputId
                leds = tonumber(o.leds_count) or 0
            end
        end
        local is_skydimo = port == "COM4" or path:find("COM4", 1, true)
            or cid:find("skydimo", 1, true) or name:find("Skydimo", 1, true)
        if is_skydimo and out_id ~= nil and tostring(out_id) ~= "" then
            return port, tostring(out_id), leds, devices
        end
    end
    return nil, nil, 0, devices
end

function P.on_start()
    dlog("starting")
    -- Лента может появиться в списке устройств позже старта расширения:
    -- раньше при первой неудаче расширение останавливалось навсегда.
    local port, output, leds, last_devices
    local tries = 0
    while true do
        port, output, leds, last_devices = find_target()
        if port and output then break end
        tries = tries + 1
        if tries % 10 == 1 then
            local dump = "?"
            if type(last_devices) == "table" then
                local okj, s = pcall(ext.json_encode, last_devices)
                if okj then dump = tostring(s):sub(1, 600) end
            end
            dlog("strip not found yet (try " .. tries .. "); devices=" .. dump)
        end
        pcall(ext.sleep, 1000)
    end
    if leds <= 0 then leds = 96 end
    dlog(string.format("strip: port=%s output=%s leds=%d", port, output, leds))

    local indices = {}
    for i = 0, leds - 1 do indices[#indices + 1] = i end

    local frame_bytes = leds * 3
    local handshake = string.char(leds % 256, math.floor(leds / 256) % 256)
    local scope = { port = port, output_id = output }
    local mode = nil          -- "paint" | "mirror": что сейчас делаем с лентой
    local last_bright = nil
    local attempt = 0
    local frames = 0

    local function enter_mode(m)
        if mode == m then return end
        if m == "mirror" then
            -- цвет по краям экрана считает сама Skydimo; светодиоды не держим
            pcall(ext.unlock_leds, port, output, indices)
            local oke, erre = pcall(ext.set_scope_effect, scope, "ScreenMirror", { autoCrop = false })
            dlog("mirror: set_scope_effect " .. tostring(oke) .. " " .. tostring(erre))
        else
            local okl2, errl2 = pcall(ext.lock_leds, port, output, indices)
            dlog("paint: lock_leds " .. tostring(okl2) .. " " .. tostring(errl2))
        end
        mode = m
        last_bright = nil
    end

    local function release()
        -- AuraFusion пропал: возвращаем ленту в обычное состояние
        if mode == "mirror" then
            pcall(ext.set_scope_brightness, scope, 100)
        elseif mode == "paint" then
            pcall(ext.unlock_leds, port, output, indices)
        end
        mode = nil
        last_bright = nil
    end

    while true do
        local okc, conn = pcall(tcp.connect, {
            host = HOST,
            port = PORT,
            connect_timeout_ms = 3000,
            read_timeout_ms = 10000,
            write_timeout_ms = 3000,
            no_delay = true,
        })
        if not okc or not conn then
            attempt = attempt + 1
            if attempt % 20 == 1 then
                dlog("connect failed: " .. tostring(conn) .. " (retrying)")
            end
            pcall(ext.sleep, 500)
        else
            attempt = 0
            dlog("connected to AuraFusion")
            local okw, errw = pcall(tcp.write_all, conn, handshake)
            if not okw then
                dlog("handshake failed: " .. tostring(errw))
            else
                while true do
                    -- каждое сообщение: 1 байт типа + полезная нагрузка
                    local okt, kind = pcall(tcp.read_exact, conn, 1, 10000)
                    if not okt or kind == nil or #kind < 1 then
                        dlog("read ended: " .. tostring(kind))
                        break
                    end
                    if kind == "B" then
                        local okr, d = pcall(tcp.read_exact, conn, 1, 3000)
                        if not okr or d == nil or #d < 1 then
                            dlog("read ended (B): " .. tostring(d))
                            break
                        end
                        enter_mode("mirror")
                        local v = d:byte(1)
                        if v > 100 then v = 100 end
                        if v ~= last_bright then
                            local oks, err = pcall(ext.set_scope_brightness, scope, v)
                            if oks then
                                last_bright = v
                            else
                                dlog("set_scope_brightness failed: " .. tostring(err))
                            end
                        end
                        frames = frames + 1
                        if frames == 1 or frames % 500 == 0 then
                            dlog("brightness msgs: " .. frames .. " last=" .. tostring(v))
                        end
                    elseif kind == "F" then
                        local okr, data = pcall(tcp.read_exact, conn, frame_bytes, 10000)
                        if not okr or data == nil or #data < frame_bytes then
                            dlog("read ended (F): " .. tostring(data))
                            break
                        end
                        enter_mode("paint")
                        local colors = {}
                        for i = 0, leds - 1 do
                            local b = i * 3
                            colors[i + 1] = { i, data:byte(b + 1), data:byte(b + 2), data:byte(b + 3) }
                        end
                        local oks, err = pcall(ext.set_leds, port, output, colors)
                        frames = frames + 1
                        if not oks then
                            dlog("set_leds failed: " .. tostring(err))
                        elseif frames == 1 or frames % 500 == 0 then
                            dlog("frames painted: " .. frames)
                        end
                    else
                        dlog("unknown message type: " .. tostring(kind:byte(1)))
                        break
                    end
                end
            end
            dlog("connection lost; will reconnect")
            release()
            pcall(tcp.close, conn)
            pcall(ext.sleep, 500)
        end
    end
end

function P.on_stop()
    dlog("stopped")
end

return P
