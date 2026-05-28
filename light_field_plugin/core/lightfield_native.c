#define WIN32_LEAN_AND_MEAN
#include <stdint.h>
#include <math.h>
#include <string.h>

#ifdef _OPENMP
#include <omp.h>
#endif

#ifdef _WIN32
#define LF_EXPORT __declspec(dllexport)
#else
#define LF_EXPORT
#endif

static inline uint8_t clamp_round_u8(float value) {
    if (value <= 0.0f) {
        return 0;
    }
    if (value >= 255.0f) {
        return 255;
    }
    return (uint8_t)(value + 0.5f);
}

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

    const int64_t source_plane = (int64_t)source_width * (int64_t)source_height * 3;
    const double inv_gamma = 1.0 / (gamma_value > 1.0e-6 ? gamma_value : 1.0e-6);
    const int use_gamma = fabs(gamma_value - 1.0) > 1.0e-6;

    int row;
    #pragma omp parallel for schedule(static)
    for (row = 0; row < batch_rows; ++row) {
        const int final_y = y_start + row;
        const double sy = (double)final_y * y_scale;
        int y0 = (int)floor(sy);
        if (y0 < 0) y0 = 0;
        if (y0 >= source_height) y0 = source_height - 1;
        int y1 = y0 + 1;
        if (y1 >= source_height) y1 = source_height - 1;
        const float ty = (float)(sy - (double)y0);
        const float wy0 = 1.0f - ty;

        uint8_t* rgb_row = rgb_out ? rgb_out + (int64_t)row * final_width * 3 : 0;
        uint8_t* bit_row = bit_out + (int64_t)row * row_bytes;
        memset(bit_row, 0, (size_t)row_bytes);

        for (int x = 0; x < final_width; ++x) {
            uint8_t rgb[3];
            const int32_t x0 = x0_map[x];
            const int32_t x1 = x1_map[x];
            const float tx = tx_map[x];
            const float wx0 = 1.0f - tx;

            for (int channel = 0; channel < 3; ++channel) {
                int view = (int)view_map[channel * final_width + x];
                if (view < 0) view = 0;
                if (view >= source_count) view = source_count - 1;
                const int64_t base = (int64_t)view * source_plane;
                const int64_t p00 = base + (((int64_t)y0 * source_width + x0) * 3 + channel);
                const int64_t p10 = base + (((int64_t)y0 * source_width + x1) * 3 + channel);
                const int64_t p01 = base + (((int64_t)y1 * source_width + x0) * 3 + channel);
                const int64_t p11 = base + (((int64_t)y1 * source_width + x1) * 3 + channel);
                const float top = (float)sources[p00] * wx0 + (float)sources[p10] * tx;
                const float bottom = (float)sources[p01] * wx0 + (float)sources[p11] * tx;
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
            if (use_gamma) {
                luma_norm = pow(luma_norm, inv_gamma);
            }
            const double darkness = 1.0 - luma_norm;
            int is_black = 0;
            if (darkness >= 1.0) {
                is_black = 1;
            } else if (darkness > 0.0) {
                const double xr = (double)x * screen_cos + (double)final_y * screen_sin;
                const double yr = -(double)x * screen_sin + (double)final_y * screen_cos;
                const double fx = xr / cell_size;
                const double fy = yr / cell_size;
                const double u = (fx - floor(fx)) * 2.0 - 1.0;
                const double v = (fy - floor(fy)) * 2.0 - 1.0;
                double metric;
                double threshold;
                if (dot_shape == 1) {
                    metric = (fabs(u) + fabs(v)) / 2.0;
                    threshold = darkness;
                } else if (dot_shape == 2) {
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
    return 0;
}
