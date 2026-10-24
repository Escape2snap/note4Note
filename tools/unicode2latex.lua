-- Unicode → LaTeX 转换过滤器（md2tex 与 tex2pdf 共用）
-- 说明：箭头必须输出为 RawInline("latex", ...)，不能塞进 Str。
-- Str 的内容会被 LaTeX 写出器转义，反斜杠会变成 \textbackslash{}，
-- 结果 PDF 里出现的是 "\ensuremath{\rightarrow}" 这串字面文本。
-- 普通文字仍原样交给 pandoc 转义（% & # _ 等符号不受影响）。

-- Unicode 上下标 → 纯文本
local SUB_SUP = {["⁺"]="+", ["⁻"]="-", ["₂"]="2", ["₃"]="3", ["₄"]="4", ["₁"]="1"}

-- Unicode 不常用箭头 → LaTeX（常用箭头 →←↑↓↔⇌ 由字体直接支持）
local ARROW_REPS = {
    -- 长/带修饰箭头
    ["⟷"] = "\\ensuremath{\\longleftrightarrow}",
    ["⟵"] = "\\ensuremath{\\longleftarrow}",
    ["⟶"] = "\\ensuremath{\\longrightarrow}",
    ["⟸"] = "\\ensuremath{\\Longleftarrow}",
    ["⟹"] = "\\ensuremath{\\Longrightarrow}",
    ["⟺"] = "\\ensuremath{\\Longleftrightarrow}",
    ["↦"] = "\\ensuremath{\\mapsto}",
    ["↔"] = "\\ensuremath{\\leftrightarrow}",
    -- 带钩箭头
    ["↩"] = "\\ensuremath{\\hookleftarrow}",
    ["↪"] = "\\ensuremath{\\hookrightarrow}",
    ["↰"] = "\\ensuremath{\\Lsh}",
    ["↱"] = "\\ensuremath{\\Rsh}",
    -- 双线箭头
    ["⇐"] = "\\ensuremath{\\Leftarrow}",
    ["⇒"] = "\\ensuremath{\\Rightarrow}",
    ["⇔"] = "\\ensuremath{\\Leftrightarrow}",
    ["⇑"] = "\\ensuremath{\\Uparrow}",
    ["⇓"] = "\\ensuremath{\\Downarrow}",
    ["⇕"] = "\\ensuremath{\\Updownarrow}",
    ["⇖"] = "\\ensuremath{\\nwarrow}",
    ["⇗"] = "\\ensuremath{\\nearrow}",
    ["⇘"] = "\\ensuremath{\\searrow}",
    ["⇙"] = "\\ensuremath{\\swarrow}",
    ["⇄"] = "\\ensuremath{\\rightleftarrows}",
    ["⇌"] = "\\ensuremath{\\rightleftharpoons}",
    ["⇋"] = "\\ensuremath{\\leftrightharpoons}",
    ["⇆"] = "\\ensuremath{\\leftrightarrows}",
    -- 长双线
    ["⟼"] = "\\ensuremath{\\longmapsto}",
    -- 带否定箭头
    ["↛"] = "\\ensuremath{\\nrightarrow}",
    ["↚"] = "\\ensuremath{\\nleftarrow}",
    ["⇍"] = "\\ensuremath{\\nLeftarrow}",
    ["⇏"] = "\\ensuremath{\\nRightarrow}",
    ["⇎"] = "\\ensuremath{\\nLeftrightarrow}",
}

-- 纯文本替换（plain 模式，避免 Unicode 字符被当成 Lua 模式）
local function replace_all(s, k, v)
    local hit = false
    local i, j = s:find(k, 1, true)
    while i do
        s = s:sub(1, i-1) .. v .. s:sub(j+1)
        hit = true
        i, j = s:find(k, 1, true)
    end
    return s, hit
end

function Str(el)
    local s = el.text
    local changed = false

    for k, v in pairs(SUB_SUP) do
        local hit
        s, hit = replace_all(s, k, v)
        changed = changed or hit
    end

    -- 逐字符扫描：普通文字 → Str（交给 pandoc 转义），箭头 → RawInline
    local buf, out = {}, {}
    for _, cp in utf8.codes(s) do
        local ch = utf8.char(cp)
        local cmd = ARROW_REPS[ch]
        if cmd then
            if #buf > 0 then
                out[#out + 1] = pandoc.Str(table.concat(buf))
                buf = {}
            end
            out[#out + 1] = pandoc.RawInline("latex", cmd)
        else
            buf[#buf + 1] = ch
        end
    end

    if #out == 0 then
        if changed then return pandoc.Str(s) end
        return nil
    end
    if #buf > 0 then out[#out + 1] = pandoc.Str(table.concat(buf)) end
    return out
end

function CodeBlock(el)
    local s = el.text
    for k, v in pairs(SUB_SUP) do
        s = replace_all(s, k, v)
    end
    el.text = s
    return el
end
