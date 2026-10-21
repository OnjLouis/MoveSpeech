typedef __SIZE_TYPE__ size_t;

enum { MAX_INPUT_BYTES = 512, MAX_SPEECH_BYTES = 256 };
extern void stock_screenreader_text(void *surface, const char *text, size_t length);

static int private_use(unsigned codepoint)
{
    return (codepoint >= 0xe000 && codepoint <= 0xf8ff)
        || (codepoint >= 0xf0000 && codepoint <= 0xffffd)
        || (codepoint >= 0x100000 && codepoint <= 0x10fffd);
}

static size_t append_word(char *output, size_t used, const char *word)
{
    while (*word && used < MAX_SPEECH_BYTES)
        output[used++] = *word++;
    return used;
}

void speak_filtered(void *surface, const unsigned char *text, size_t length, unsigned kind)
{
    char output[MAX_SPEECH_BYTES];
    size_t used = 0;
    int space = 0;
    if (!surface || (!text && length) || length > MAX_INPUT_BYTES)
        return;
    if (kind == 1)
        used = append_word(output, used, "Gain");
    else if (kind == 2)
        used = append_word(output, used, "Number of slices");
    space = used != 0;

    for (size_t index = 0; index < length;) {
        size_t start = index;
        unsigned lead = text[index++];
        unsigned codepoint = lead;
        unsigned count = 0;
        unsigned minimum = 0;
        if (lead >= 0xc2 && lead <= 0xdf) {
            count = 1;
            codepoint &= 0x1f;
            minimum = 0x80;
        } else if (lead >= 0xe0 && lead <= 0xef) {
            count = 2;
            codepoint &= 0xf;
            minimum = 0x800;
        } else if (lead >= 0xf0 && lead <= 0xf4) {
            count = 3;
            codepoint &= 7;
            minimum = 0x10000;
        } else if (lead >= 0x80) {
            continue;
        }
        if (length - index < count)
            break;
        unsigned valid = 1;
        for (unsigned remaining = count; remaining; --remaining) {
            unsigned next = text[index];
            if ((next & 0xc0) != 0x80) {
                valid = 0;
                break;
            }
            codepoint = (codepoint << 6) | (next & 0x3f);
            ++index;
        }
        if (!valid || codepoint < minimum || codepoint > 0x10ffff
            || (codepoint >= 0xd800 && codepoint <= 0xdfff))
            continue;
        if (private_use(codepoint))
            continue;
        if (codepoint <= 0x20 || (codepoint >= 0x7f && codepoint <= 0x9f)) {
            space = used != 0;
            continue;
        }
        size_t bytes = index - start;
        unsigned numeric = (kind == 1 || kind == 3)
            && (start == 0 || text[start - 1] <= 0x20)
            && index < length && text[index] >= '0' && text[index] <= '9';
        const char *sign = numeric && codepoint == '+' ? "plus "
            : numeric && (codepoint == '-' || codepoint == 0x2212) ? "minus " : (const char *)0;
        size_t needed = sign ? (sign[0] == 'p' ? 5 : 6) : bytes;
        if (used + (space ? 1 : 0) + needed > MAX_SPEECH_BYTES)
            break;
        if (space)
            output[used++] = ' ';
        space = 0;
        if (sign) {
            used = append_word(output, used, sign);
        } else {
            for (size_t offset = 0; offset < bytes; ++offset)
                output[used++] = text[start + offset];
        }
    }
    while (used && output[used - 1] == ' ')
        --used;
    stock_screenreader_text(surface, output, used);
}

static size_t copy_native_string(unsigned char *output, size_t used, const unsigned char *object)
{
    size_t length = object[0] >> 1;
    const unsigned char *data = object + 1;
    if (object[0] & 1) {
        length = *(const size_t *)(object + 8);
        data = *(const unsigned char *const *)(object + 16);
    }
    if (!data || length > MAX_INPUT_BYTES - used)
        return used;
    for (size_t index = 0; index < length; ++index)
        output[used++] = data[index];
    return used;
}

static int speak_waveform_header(void *surface, const unsigned char *waveform)
{
    /* The native waveform header is either one string or a name/value pair.
       Read only the active variant, exactly as its original renderer does. */
    const unsigned char *header = waveform + 0x4f8;
    unsigned type = *(const unsigned *)(header + 0x38);
    if (waveform[0xd0] && header[0x40] && type <= 1) {
        unsigned char text[MAX_INPUT_BYTES];
        size_t used = copy_native_string(text, 0, header + 8);
        if (type == 1 && used < MAX_INPUT_BYTES) {
            text[used++] = ' ';
            used = copy_native_string(text, used, header + 0x20);
        }
        if (used) {
            speak_filtered(surface, text, used, 3);
            return 1;
        }
    }
    return 0;
}

void speak_slice(void *surface, const unsigned char *waveform)
{
    if (!speak_waveform_header(surface, waveform))
        speak_filtered(surface, (const unsigned char *)"Slice sample", 12, 0);
}

void speak_sample(void *surface, const unsigned char *waveform)
{
    /* The parameter view tail-calls the waveform renderer only for a loaded
       sample. Keep its normal no-header waveform silent between edits. */
    speak_waveform_header(surface, waveform);
}

void speak_velocity(void *surface, const unsigned char *pair, size_t count)
{
    if (!surface || !pair || count != 2)
        return;
    unsigned char text[MAX_INPUT_BYTES];
    size_t used = copy_native_string(text, 0, pair);
    if (!used || used >= MAX_INPUT_BYTES)
        return;
    text[used++] = ' ';
    size_t length = copy_native_string(text, used, pair + 24);
    if (length == used)
        return;
    speak_filtered(surface, text, length, 0);
}
