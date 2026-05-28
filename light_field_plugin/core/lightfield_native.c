#define WIN32_LEAN_AND_MEAN
#include <stdint.h>
#include <math.h>
#include <string.h>

#ifdef _WIN32
#include <windows.h>
#define LF_EXPORT __declspec(dllexport)
#else
#define LF_EXPORT
#endif

typedef struct LfBatchJob {
    const uint8_t* sources;
    int source_count;
    int source_width;
    int source_height;
    int final_width;
    int final_height;
    int y_start;
    int row_begin;
    int row_end;
    const int16_t* view_map;
    const int32_t* x0_map;
    const int32_t* x1_map;
    const float* tx_map;
    double y_scale;
    double screen_cos;
    double screen_sin;
    double cell_size;
    double inv_gamma;
    int use_gamma;
    int dot_shape;
    uint8_t* rgb_out;
    uint8_t* bit_out;
    int row_bytes;
} LfBatchJob;

static inline uint8_t clamp_round_u8(float value) {
    if (value <= 0.0f) {
        return 0;
    }
    if (value >= 255.0f) {
        return 255;
    }
    return (uint8_t)(value + 0.5f);
}

static void lf_process_rows(const LfBatchJob* job) {
    const int64_t source_plane = (int64_t)job->source_width * (int64_t)job->source_height * 3;

    for (int row = job->row_begin; row < job->row_end; ++row) {
        const int final_y = job->y_start + row;
        const double sy = (double)final_y * job->y_scale;
        int y0 = (int)floor(sy);
        if (y0 < 0) y0 = 0;
        if (y0 >= job->source_height) y0 = job->source_height - 1;
        int y1 = y0 + 1;
        if (y1 >= job->source_height) y1 = job->source_height - 1;
        const float ty = (float)(sy - (double)y0);
        const float wy0 = 1.0f - ty;

        uint8_t* rgb_row = job->rgb_out ? job->rgb_out + (int64_t)row * job->final_width * 3 : 0;
        uint8_t* bit_row = job->bit_out + (int64_t)row * job->row_bytes;
        memset(bit_row, 0, (size_t)job->row_bytes);

        for (int x = 0; x < job->final_width; ++x) {
            uint8_t rgb[3];
            const int32_t x0 = job->x0_map[x];
            const int32_t x1 = job->x1_map[x];
            const float tx = job->tx_map[x];
            const float wx0 = 1.0f - tx;

            for (int channel = 0; channel < 3; ++channel) {
                int view = (int)job->view_map[channel * job->final_width + x];
                if (view < 0) view = 0;
                if (view >= job->source_count) view = job->source_count - 1;
                const int64_t base = (int64_t)view * source_plane;
                const int64_t p00 = base + (((int64_t)y0 * job->source_width + x0) * 3 + channel);
                const int64_t p10 = base + (((int64_t)y0 * job->source_width + x1) * 3 + channel);
                const int64_t p01 = base + (((int64_t)y1 * job->source_width + x0) * 3 + channel);
                const int64_t p11 = base + (((int64_t)y1 * job->source_width + x1) * 3 + channel);
                const float top = (float)job->sources[p00] * wx0 + (float)job->sources[p10] * tx;
                const float bottom = (float)job->sources[p01] * wx0 + (float)job->sources[p11] * tx;
                rgb[channel] = clamp_round_u8(top * wy0 + bottom * ty);
            }

            if (rgb_row) {
                const int target = x * 3;
                rgb_row[target] = rgb[0];
                rgb_row[target + 1] = rgb[1];
                rgb_row[target + 2] = rgb[2];
            }

            const double luma = 0.2126 * (double)rgb[0] + 0.7152 * (double)rgb[1] + 0.0722 * (double)rgb[2];
            double luma_norm = luma / 255.0;
            if (luma_norm < 0.0) luma_norm = 0.0;
            if (luma_norm > 1.0) luma_norm = 1.0;
            if (job->use_gamma) {
                luma_norm = pow(luma_norm, job->inv_gamma);
            }
            const double darkness = 1.0 - luma_norm;
            int is_black = 0;
            if (darkness >= 1.0) {
                is_black = 1;
            } else if (darkness > 0.0) {
                const double xr = (double)x * job->screen_cos + (double)final_y * job->screen_sin;
                const double yr = -(double)x * job->screen_sin + (double)final_y * job->screen_cos;
                const double fx = xr / job->cell_size;
                const double fy = yr / job->cell_size;
                const double u = (fx - floor(fx)) * 2.0 - 1.0;
                const double v = (fy - floor(fy)) * 2.0 - 1.0;
                double metric;
                double threshold;
                if (job->dot_shape == 1) {
                    metric = (fabs(u) + fabs(v)) / 2.0;
                    threshold = darkness;
                } else if (job->dot_shape == 2) {
                    metric = sqrt(u * u + (v / 0.65) * (v / 0.65));
                    threshold = sqrt(darkness);
                } else {
                    metric = sqrt(u * u + v * v);
                    threshold = sqrt(darkness);
                }
                is_black = metric <= threshold;
            }
            if (is_black) {
                bit_row[x >> 3] |= (uint8_t)(0x80 >> (x & 7));
            }
        }
    }
}

#ifdef _WIN32
static DWORD WINAPI lf_worker_thread(LPVOID param) {
    lf_process_rows((const LfBatchJob*)param);
    return 0;
}
#endif

LF_EXPORT int lf_generate_am_batch(
    const uint8_t* sources,
    int source_count,
    int source_width,
    int source_height,
    int final_width,
    int final_height,
    int y_start,
    int batch_rows,
    const int16_t* view_map,
    const int32_t* x0_map,
    const int32_t* x1_map,
    const float* tx_map,
    double y_scale,
    double screen_cos,
    double screen_sin,
    double cell_size,
    double gamma_value,
    int dot_shape,
    uint8_t* rgb_out,
    uint8_t* bit_out,
    int row_bytes
) {
    if (!sources || !view_map || !x0_map || !x1_map || !tx_map || !bit_out) {
        return 1;
    }
    if (source_count <= 0 || source_width <= 0 || source_height <= 0 ||
        final_width <= 0 || final_height <= 0 || batch_rows <= 0 || row_bytes <= 0) {
        return 2;
    }

    LfBatchJob base;
    memset(&base, 0, sizeof(base));
    base.sources = sources;
    base.source_count = source_count;
    base.source_width = source_width;
    base.source_height = source_height;
    base.final_width = final_width;
    base.final_height = final_height;
    base.y_start = y_start;
    base.view_map = view_map;
    base.x0_map = x0_map;
    base.x1_map = x1_map;
    base.tx_map = tx_map;
    base.y_scale = y_scale;
    base.screen_cos = screen_cos;
    base.screen_sin = screen_sin;
    base.cell_size = cell_size;
    base.inv_gamma = 1.0 / (gamma_value > 1.0e-6 ? gamma_value : 1.0e-6);
    base.use_gamma = fabs(gamma_value - 1.0) > 1.0e-6;
    base.dot_shape = dot_shape;
    base.rgb_out = rgb_out;
    base.bit_out = bit_out;
    base.row_bytes = row_bytes;

#ifdef _WIN32
    SYSTEM_INFO info;
    GetSystemInfo(&info);
    int thread_count = (int)info.dwNumberOfProcessors;
    if (thread_count < 1) thread_count = 1;
    if (thread_count > batch_rows) thread_count = batch_rows;
    if (thread_count > 64) thread_count = 64;
    if (thread_count <= 1) {
        base.row_begin = 0;
        base.row_end = batch_rows;
        lf_process_rows(&base);
        return 0;
    }

    HANDLE handles[64];
    LfBatchJob jobs[64];
    int handle_count = 0;
    for (int i = 0; i < thread_count; ++i) {
        jobs[i] = base;
        jobs[i].row_begin = (int)(((int64_t)i * batch_rows) / thread_count);
        jobs[i].row_end = (int)(((int64_t)(i + 1) * batch_rows) / thread_count);
        handles[handle_count] = CreateThread(0, 0, lf_worker_thread, &jobs[i], 0, 0);
        if (handles[handle_count]) {
            ++handle_count;
        } else {
            lf_process_rows(&jobs[i]);
        }
    }
    if (handle_count > 0) {
        WaitForMultipleObjects((DWORD)handle_count, handles, TRUE, INFINITE);
        for (int i = 0; i < handle_count; ++i) {
            CloseHandle(handles[i]);
        }
    }
#else
    base.row_begin = 0;
    base.row_end = batch_rows;
    lf_process_rows(&base);
#endif
    return 0;
}
