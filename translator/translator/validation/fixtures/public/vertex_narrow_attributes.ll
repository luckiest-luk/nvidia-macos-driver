; Locally authored regression input; the upstream file was omitted from v2.3.7.
; Fetch uchar and ushort as 32-bit vertex inputs, then narrow for body arithmetic.
define <{ <4 x float> }> @narrow_attributes(i8 %small, i16 %medium) {
entry:
  %smallwide = zext i8 %small to i32
  %mediumwide = zext i16 %medium to i32
  %x = uitofp i32 %smallwide to float
  %y = uitofp i32 %mediumwide to float
  %v0 = insertelement <4 x float> undef, float %x, i32 0
  %v1 = insertelement <4 x float> %v0, float %y, i32 1
  %v2 = insertelement <4 x float> %v1, float 0.000000e+00, i32 2
  %v3 = insertelement <4 x float> %v2, float 1.000000e+00, i32 3
  %out = insertvalue <{ <4 x float> }> undef, <4 x float> %v3, 0
  ret <{ <4 x float> }> %out
}

!air.vertex = !{!0}
!0 = !{ptr @narrow_attributes, !1, !3}
!1 = !{!2}
!2 = !{!"air.position", !"air.arg_type_name", !"float4"}
!3 = !{!4, !5}
!4 = !{i32 0, !"air.vertex_input", !"air.location_index", i32 0, i32 1, !"air.arg_type_name", !"uchar", !"air.arg_name", !"small"}
!5 = !{i32 1, !"air.vertex_input", !"air.location_index", i32 1, i32 1, !"air.arg_type_name", !"ushort", !"air.arg_name", !"medium"}
