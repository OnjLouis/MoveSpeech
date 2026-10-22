typedef __SIZE_TYPE__ size_t;

enum { MAX_INPUT_BYTES = 512, MAX_SPEECH_BYTES = 256 };
enum {
    WIFI_NEXT_CHARACTER_SET = 0xe237,
    WIFI_DELETE_CHARACTER = 0xe239,
    WIFI_CONFIRM_PASSWORD = 0xe23a
};
enum {
    SAMPLE_MARKER_POSITION = 0x578,
    SAMPLE_MARKER_KIND = 0x584,
    SAMPLE_MARKER_ACTIVE = 0x588,
    SAMPLE_START_MARKER = 1,
    PERCENT_HUNDREDTHS = 10000,
    AUDIO_RANGE_START = 0x5b8,
    AUDIO_RANGE_END = 0x5c0,
    AUDIO_RANGE_ACTIVE = 0x5c8,
    MAX_AUDIO_RANGE_POSITION = 10000,
    HEADER_SLICE = 0,
    HEADER_SAMPLE = 1
};
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

static size_t append_percentage(unsigned char *text, size_t used, double position)
{
    text[used++] = ' ';
    if (position < 0) {
        const char *sign = "minus ";
        while (*sign)
            text[used++] = *sign++;
        position = -position;
    }
    unsigned hundredths = (unsigned)(position * PERCENT_HUNDREDTHS + 0.5);
    unsigned whole = hundredths / 100;
    unsigned char digits[10];
    unsigned count = 0;
    do {
        digits[count++] = '0' + whole % 10;
        whole /= 10;
    } while (whole);
    while (count)
        text[used++] = digits[--count];
    if (hundredths % 100) {
        text[used++] = '.';
        text[used++] = '0' + (hundredths / 10) % 10;
        if (hundredths % 10)
            text[used++] = '0' + hundredths % 10;
    }
    const char *unit = " percent";
    while (*unit)
        text[used++] = *unit++;
    return used;
}

static int start_label(const unsigned char *text, size_t length, int audio)
{
    size_t index = 0;
    if (audio && length > 5) {
        if (length > 20 || text[0] != 'B' || text[1] != 'a' || text[2] != 'r'
            || text[3] != ' ' || text[4] < '1' || text[4] > '9')
            return 0;
        index = 4;
        while (index < length && text[index] >= '0' && text[index] <= '9')
            ++index;
        if (index - 4 > 10 || index >= length || text[index++] != ' ')
            return 0;
    }
    return length - index == 5 && text[index] == 'S' && text[index+1] == 't'
        && text[index+2] == 'a' && text[index+3] == 'r' && text[index+4] == 't';
}

static size_t append_start_position(unsigned char *text, size_t used, const unsigned char *waveform)
{
    double position;
    if (!waveform[SAMPLE_MARKER_ACTIVE]) {
        if (!waveform[AUDIO_RANGE_ACTIVE])
            return used;
        position = *(const double *)(waveform + AUDIO_RANGE_START);
        double end = *(const double *)(waveform + AUDIO_RANGE_END);
        if (!(position >= -MAX_AUDIO_RANGE_POSITION && position <= MAX_AUDIO_RANGE_POSITION
              && end > position && end <= MAX_AUDIO_RANGE_POSITION))
            return used;
    } else {
        if (*(const unsigned *)(waveform + SAMPLE_MARKER_KIND) != SAMPLE_START_MARKER)
            return used;
        position = *(const double *)(waveform + SAMPLE_MARKER_POSITION);
        if (!(position >= 0 && position <= 1))
            return used;
    }
    /* Both native positions are fractions of the complete sample. Audio clips
       use the range edge and can extend outside it; pads use an optional marker. */
    return append_percentage(text, used, position);
}

static int speak_waveform_header(void *surface, const unsigned char *waveform, int kind)
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
        if (kind == HEADER_SAMPLE && type == 0 && start_label(text, used, 1))
            used = append_start_position(text, used, waveform);
        if (used) {
            speak_filtered(surface, text, used, 3);
            return 1;
        }
    }
    return 0;
}

void speak_slice(void *surface, const unsigned char *waveform)
{
    if (!speak_waveform_header(surface, waveform, HEADER_SLICE))
        speak_filtered(surface, (const unsigned char *)"Slice sample", 12, 0);
}

void speak_sample(void *surface, const unsigned char *waveform)
{
    /* The parameter view tail-calls the waveform renderer only for a loaded
       sample. Keep its normal no-header waveform silent between edits. */
    speak_waveform_header(surface, waveform, HEADER_SAMPLE);
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

void speak_wifi_character(void *surface, unsigned codepoint, unsigned next_set)
{
    /* This is the selected key in the native alphabet, not the password text. */
    const char *label = (const char *)0;
    char letter[16];
    size_t used = 0;
    if ((codepoint >= 'a' && codepoint <= 'z') || (codepoint >= '0' && codepoint <= '9')) {
        letter[0] = (char)codepoint;
        speak_filtered(surface, (const unsigned char *)letter, 1, 0);
        return;
    }
    if (codepoint >= 'A' && codepoint <= 'Z')
        used = append_word(letter, used, "Cap ");
    if (used) {
        letter[used++] = (char)codepoint;
        speak_filtered(surface, (const unsigned char *)letter, used, 0);
        return;
    }
    switch (codepoint) {
    case ' ': label = "Space"; break;
    case '.': label = "Full stop"; break;
    case '-': label = "Hyphen"; break;
    case '_': label = "Underscore"; break;
    case '!': label = "Exclamation mark"; break;
    case '@': label = "At sign"; break;
    case '#': label = "Hash"; break;
    case '$': label = "Dollar sign"; break;
    case '%': label = "Percent sign"; break;
    case '^': label = "Caret"; break;
    case '&': label = "Ampersand"; break;
    case '*': label = "Asterisk"; break;
    case '`': label = "Backtick"; break;
    case '\'': label = "Apostrophe"; break;
    case '"': label = "Double quote"; break;
    case ';': label = "Semicolon"; break;
    case ':': label = "Colon"; break;
    case '~': label = "Tilde"; break;
    case '=': label = "Equals sign"; break;
    case '+': label = "Plus sign"; break;
    case ',': label = "Comma"; break;
    case '?': label = "Question mark"; break;
    case '|': label = "Vertical bar"; break;
    case '\\': label = "Backslash"; break;
    case '/': label = "Slash"; break;
    case '(': label = "Left parenthesis"; break;
    case ')': label = "Right parenthesis"; break;
    case '[': label = "Left square bracket"; break;
    case ']': label = "Right square bracket"; break;
    case '{': label = "Left brace"; break;
    case '}': label = "Right brace"; break;
    case '<': label = "Less than"; break;
    case '>': label = "Greater than"; break;
    case WIFI_NEXT_CHARACTER_SET:
        switch (next_set) {
        case 0: label = "Switch to lowercase letters"; break;
        case 1: label = "Switch to uppercase letters"; break;
        case 2: label = "Switch to numbers and symbols"; break;
        case 3: label = "Switch to more symbols"; break;
        }
        break;
    case WIFI_DELETE_CHARACTER: label = "Delete character"; break;
    case WIFI_CONFIRM_PASSWORD: label = "Confirm password"; break;
    }
    if (label) {
        char text[MAX_SPEECH_BYTES];
        size_t length = append_word(text, 0, label);
        speak_filtered(surface, (const unsigned char *)text, length, 0);
    }
}
