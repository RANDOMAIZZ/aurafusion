-- AuraFusion bridge extension.
-- Connects to AuraFusion over raw TCP (127.0.0.1:8766), reads per-frame
-- color lines "r,g,b | r,g,b | ..." (triplets), and paints the strip via
-- ext.set_leds.

local tcp = ext.net.tcp

local P = {}

local HOST = "127.0.0.1"
local PORT = 8766

local function log(msg)
    pcall(ext.log, "aurafusion: " .. tostring(msg))
end

local function find_target()
    local ok, devices = pcall(ext.get_devices)
    if not ok then
        log("get_devices failed: " .. tostring(devices))
        return nil, nil, 0
    end
    if type(devices) ~= "table" then
        log("get_devices: not a table: " .. tostring(devices))
        return nil, nil, 0
    end
    for _, dev in pairs(devices) do
        local port = tostring(dev.port or "")
        local path = tostring(dev.device_path or "")
        local cid = tostring(dev.controller_id or "")
        local out_id = nil
        local leds = 0
        local outputs = dev.outputs
        if type(outputs) == "table" then
            local o = outputs[1]
            if type(o) == "table" then
                out_id = tostring(o.id or o.outputId or "")
                leds = tonumber(o.leds_count) or 0
            end
        end
        local name = tostring(dev.name or dev.nickname or "")
        log(string.format("device: port=%s path=%s cid=%s output=%s leds=%d name=%s",
            port, path, cid, tostring(out_id), leds, name))
        if port == "COM4" or path:find("COM4", 1, true)
            or cid:find("skydimo", 1, true) or name:find("Skydimo", 1, true)
            or cid == "" and leds > 0 then
            return port, out_id, leds
        end
    end
    log("Skydimo strip not found in device list")
    return nil, nil, 0
end

local function parse_frame(line, leds)
    -- line format: comma separated RGB byte triplets, up to leds*3 numbers
    local colors = {}
    local idx = 0
    local n = leds * 3
    for v in line:gmatch("%d+") do
        if idx < n then
            local rank = idx % 3
            local li = idx // 3 + 1
            local val = tonumber(v) or 0
            if val > 255 then val = 255 end
            if colors[li] == nil then
                colors[li] = { r = 0, g = 0, b = 0 }
            end
            if rank == 0 then colors[li].r = val
            elseif rank == 1 then colors[li].g = val
            else colors[li].b = val end
        end
        idx = idx + 1
        if idx >= n then break end
    end
    -- pad missing leds with black
    while #colors < leds do
        colors[#colors + 1] = { r = 0, g = 0, b = 0 }
    end
    return #colors > 0 and colors or nil
end

function P.on_start()
    log("starting")
    local port, output, leds = find_target()
    if not port or not output then
        log("no strip found; stopping")
        return
    end
    if leds <= 0 then leds = 96 end
    log(string.format("strip: port=%s output=%s leds=%d", tostring(port), output, leds))

    local attempt = 0
    while true do
        local okc, conn = pcall(tcp.connect, {
            host = HOST,
            port = PORT,
            connect_timeout_ms = 3000,
            read_timeout_ms = 3000,
            write_timeout_ms = 3000,
            no_delay = true,
        })
        if not okc or not conn then
            attempt = attempt + 1
            if attempt % 20 == 1 then
                log("connect failed: " .. tostring(conn) .. " (retrying)")
            end
            pcall(ext.sleep, 500)
        else
            attempt = 0
            log("connected to AuraFusion")

            local buf = ""
            while true do
                local okr, data = pcall(tcp.read_exact, conn, 1, 3000)
                if not okr or data == nil then
                    break  -- connection lost or timeout -> reconnect
                end
                buf = buf .. data
                local pos = buf:find("\n", 1, true)
                if pos then
                    local line = buf:sub(1, pos)
                    buf = buf:sub(pos + 1)
                    local colors = parse_frame(line, leds)
                    if colors then
                        local oks, err = pcall(ext.set_leds, port, output, colors)
                        if not oks then
                            log("set_leds failed: " .. tostring(err))
                        end
                    end
                end
            end
            log("connection lost; will reconnect")
            pcall(tcp.close, conn)
            pcall(ext.sleep, 500)
        end
    end
end

function P.on_stop()
    log("stopped")
end

return P
