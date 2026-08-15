/* SPDX-License-Identifier: GPL-3.0-or-later */
#ifndef INCLUDED_CORA_API_H
#define INCLUDED_CORA_API_H

#include <gnuradio/attributes.h>

#ifdef gnuradio_cora_EXPORTS
#define CORA_API __GR_ATTR_EXPORT
#else
#define CORA_API __GR_ATTR_IMPORT
#endif

#endif
