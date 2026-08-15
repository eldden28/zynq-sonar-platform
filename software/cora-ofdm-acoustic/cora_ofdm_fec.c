// SPDX-License-Identifier: MIT
/*
 * Hard-decision Viterbi decoder for the Cora acoustic modem.
 *
 * Keeping this hot loop in C avoids thousands of tiny NumPy operations on
 * the Cortex-A9. The Python modem retains a portable NumPy fallback.
 */

#include <stdint.h>
#include <stdlib.h>

#define CORA_STATE_BITS 6u
#define CORA_STATES (1u << CORA_STATE_BITS)
#define CORA_GENERATOR_0 0171u
#define CORA_GENERATOR_1 0133u
#define CORA_INFINITY (UINT32_MAX / 4u)

static uint8_t parity(unsigned int value)
{
    return (uint8_t)(__builtin_popcount(value) & 1u);
}

static int cora_viterbi_decode_impl(
    const uint8_t *encoded,
    const uint8_t *valid,
    size_t encoded_length,
    uint8_t *decoded,
    size_t decoded_length)
{
    uint32_t metrics[CORA_STATES];
    uint32_t next_metrics[CORA_STATES];
    uint8_t *decisions;
    size_t steps;
    size_t step;
    unsigned int state;

    if (encoded == NULL || decoded == NULL || (encoded_length & 1u) != 0u)
        return -1;

    steps = encoded_length / 2u;
    if (steps < CORA_STATE_BITS || decoded_length != steps - CORA_STATE_BITS)
        return -2;

    decisions = malloc(steps * CORA_STATES);
    if (decisions == NULL)
        return -3;

    for (state = 0; state < CORA_STATES; ++state)
        metrics[state] = CORA_INFINITY;
    metrics[0] = 0;

    for (step = 0; step < steps; ++step) {
        const uint8_t received_0 = encoded[2u * step];
        const uint8_t received_1 = encoded[2u * step + 1u];
        const uint8_t valid_0 = valid == NULL ? 1u : valid[2u * step];
        const uint8_t valid_1 =
            valid == NULL ? 1u : valid[2u * step + 1u];

        if (valid_0 > 1u || valid_1 > 1u ||
            (valid_0 && received_0 > 1u) ||
            (valid_1 && received_1 > 1u)) {
            free(decisions);
            return -4;
        }

        for (state = 0; state < CORA_STATES; ++state) {
            const unsigned int input_bit = state & 1u;
            const unsigned int predecessor_0 = state >> 1u;
            const unsigned int predecessor_1 =
                predecessor_0 | (CORA_STATES >> 1u);
            const unsigned int register_0 =
                (predecessor_0 << 1u) | input_bit;
            const unsigned int register_1 =
                (predecessor_1 << 1u) | input_bit;
            const uint32_t cost_0 =
                valid_0 *
                    (parity(register_0 & CORA_GENERATOR_0) != received_0) +
                valid_1 *
                    (parity(register_0 & CORA_GENERATOR_1) != received_1);
            const uint32_t cost_1 =
                valid_0 *
                    (parity(register_1 & CORA_GENERATOR_0) != received_0) +
                valid_1 *
                    (parity(register_1 & CORA_GENERATOR_1) != received_1);
            const uint32_t candidate_0 = metrics[predecessor_0] + cost_0;
            const uint32_t candidate_1 = metrics[predecessor_1] + cost_1;
            const uint8_t choice = candidate_1 < candidate_0;

            decisions[step * CORA_STATES + state] = choice;
            next_metrics[state] = choice ? candidate_1 : candidate_0;
        }

        for (state = 0; state < CORA_STATES; ++state)
            metrics[state] = next_metrics[state];
    }

    /* The six zero tail bits force the encoder back to state zero. */
    state = 0;
    for (step = steps; step-- > 0;) {
        const uint8_t choice = decisions[step * CORA_STATES + state];

        if (step < decoded_length)
            decoded[step] = (uint8_t)(state & 1u);
        state = (state >> 1u) | (choice ? (CORA_STATES >> 1u) : 0u);
    }

    free(decisions);
    return 0;
}

int cora_viterbi_decode(
    const uint8_t *encoded,
    size_t encoded_length,
    uint8_t *decoded,
    size_t decoded_length)
{
    return cora_viterbi_decode_impl(
        encoded,
        NULL,
        encoded_length,
        decoded,
        decoded_length);
}

int cora_viterbi_decode_masked(
    const uint8_t *encoded,
    const uint8_t *valid,
    size_t encoded_length,
    uint8_t *decoded,
    size_t decoded_length)
{
    if (valid == NULL)
        return -1;
    return cora_viterbi_decode_impl(
        encoded,
        valid,
        encoded_length,
        decoded,
        decoded_length);
}
