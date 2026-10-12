"""Manifest/source-clock truth and bounded image metadata, without GPU or network."""
import copy
import unittest
from unittest import mock
from PIL import Image
from tools import strata_video as V


class ManifestTruth(unittest.TestCase):
    def manifest(self):
        return V.sample_images([Image.new('RGB',(32,24)) for _ in range(3)],
                               timestamps=[1,2,4],source_duration_s=6,time_origin_s=12,
                               source_indices=[10,20,40],preselected=True,start_s=1,end_s=5,
                               max_frames=4).manifest

    def test_coverage_cannot_claim_continuous_observation(self):
        bad=self.manifest()
        bad['coverage']['continuous_observation']=True
        with self.assertRaisesRegex(ValueError,'coverage'):
            V.validate_manifest(bad)

    def test_coverage_counts_times_gaps_and_window_match_frames(self):
        for field,value in (('sample_count',99),('sample_count',True),('first_sample_s',0),
                            ('last_sample_s',5),('max_sample_gap_s',0),('window_start_s',0),
                            ('window_end_s',6),('frame_cap_applied',True)):
            bad=self.manifest()
            bad['coverage'][field]=value
            with self.subTest(field=field),self.assertRaisesRegex(ValueError,'coverage'):
                V.validate_manifest(bad)

    def test_source_frame_mapping_types_order_and_known_count_are_checked(self):
        for field,value in (('source_index',False),('source_index',-1),('source_index',1.5),
                            ('source_index',10),('source_index',0),('input_frame_index',False),
                            ('input_frame_index',21)):
            bad=self.manifest()
            bad['frames'][1][field]=value
            with self.subTest(field=field,value=value),self.assertRaises(ValueError):
                V.validate_manifest(bad)
        bad=self.manifest()
        bad['source']['input_frame_count']=30
        with self.assertRaises(ValueError):
            V.validate_manifest(bad)

    def test_known_source_frame_count_and_expected_count_reject_bool_or_fraction(self):
        for count in (False,True,0,2.5):
            bad=self.manifest()
            bad['source']['input_frame_count']=count
            with self.subTest(count=count),self.assertRaises(ValueError):
                V.validate_manifest(bad)
        single=V.sample_images([Image.new('RGB',(32,24))],max_frames=1).manifest
        for count in (True,1.0):
            with self.subTest(count=count),self.assertRaises(ValueError):
                V.validate_manifest(single,count)

    def test_optional_minimal_and_opaque_metadata_remain_compatible(self):
        base=self.manifest()
        base.pop('coverage')
        base['source']['custom_note']='User provenance remains opaque'
        expected=copy.deepcopy(base)
        self.assertEqual(V.validate_manifest(base),expected)
        base['coverage']={'custom_note':'Original custom coverage note'}
        self.assertEqual(V.validate_manifest(base),base)

    def test_valid_sparse_source_mapping_and_trimmed_coverage_are_kept(self):
        base=self.manifest()
        base['source']['input_frame_count']=50
        for frame in base['frames']:
            frame['input_frame_index']=frame['source_index']
        self.assertEqual(V.validate_manifest(base),base)
        self.assertEqual(base['coverage']['first_sample_s'],1)
        self.assertEqual(base['coverage']['last_sample_s'],4)


class ImageSourceMetadata(unittest.TestCase):
    def test_exhausted_stream_metadata_counts_must_match_read_images(self):
        for options in ({'timestamps':[0,1,2]}, {'source_indices':[10,20,30]}):
            pictures=(Image.new('RGB',(32,24)) for _ in range(2))
            with self.subTest(options=options),self.assertRaisesRegex(ValueError,'counts'):
                V.sample_images(pictures,source_duration_s=3,max_frames=4,**options)

    def test_sized_image_metadata_mismatch_fails_before_conversion(self):
        pictures=[Image.new('RGB',(32,24)) for _ in range(3)]
        for options in ({'timestamps':[0]}, {'timestamps':[0,1,2,3]}, {'source_indices':[10]},
                        {'source_indices':[10,20,30,40]}):
            with self.subTest(options=options),mock.patch.object(V,'bounded_image') as convert:
                with self.assertRaisesRegex(ValueError,'counts'):
                    V.sample_images(pictures,source_duration_s=3,max_frames=1,**options)
                convert.assert_not_called()

    def test_sample_cap_does_not_claim_entire_image_input_was_read(self):
        pictures=[Image.new('RGB',(32,24)) for _ in range(4)]
        sampled=V.sample_images(pictures,fps=1,max_frames=1)
        self.assertEqual(len(sampled.frames),1)
        self.assertEqual(sampled.manifest['sampling']['decoded_frames'],1)
        self.assertIs(sampled.manifest['coverage']['decode_completed'],False)

    def test_sized_input_fully_read_keeps_completion_true(self):
        pictures=[Image.new('RGB',(32,24)) for _ in range(2)]
        sampled=V.sample_images(pictures,fps=1,sampling_fps=1,max_frames=2)
        self.assertEqual(sampled.manifest['sampling']['decoded_frames'],2)
        self.assertIs(sampled.manifest['coverage']['decode_completed'],True)


if __name__=='__main__':
    unittest.main()
